"""OCP extension example and tests; no example policy is enabled in production."""

import os
import secrets
import unittest
from collections.abc import Sequence
from datetime import datetime, timezone
from unittest.mock import Mock, patch

if os.environ.get("RUN_DATABASE_TESTS") != "1":
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("FRONTEND_ORIGIN", "http://localhost:3000")

from app.core.exceptions import RegistrationValidationError
from app.dependencies.registration import get_membership_rules
from app.main import app
from app.models.enums import FunctionStatus, MinistryStatus
from app.models.function import Function
from app.models.ministry import Ministry
from app.models.ministry_member import MinistryMember
from app.repositories.membership import MembershipRepository
from app.schemas.membership import MembershipCreate
from app.services.membership_rules import MembershipRule
from app.services.membership_validator import MembershipValidator


class RequireFunctionForMinistry:
    """Hypothetical policy: this ministry requires at least one selected function.

    Implements MembershipRule structurally; it is defined only in the test suite.
    """

    def __init__(self, ministry_id: int):
        self.ministry_id = ministry_id

    def validate(self, ministry: Ministry, functions: Sequence[Function]) -> None:
        if ministry.id == self.ministry_id and not functions:
            raise RegistrationValidationError("Select at least one function for this ministry.")


class MembershipRuleTests(unittest.TestCase):
    def setUp(self):
        self.repository = Mock(spec=MembershipRepository)
        self.ministry = Ministry(id=7, name="Alabanza", status=MinistryStatus.ACTIVE)
        self.function = Function(id=11, ministry_id=7, name="Guitar", status=FunctionStatus.ACTIVE)
        self.repository.get_ministry.return_value = self.ministry
        self.repository.get_functions.return_value = [self.function]
        self.selection = MembershipCreate(ministry_id=7, function_ids=[11])
        self.rule = Mock(spec=MembershipRule)
        self.validator = MembershipValidator(self.repository, (self.rule,))

    def test_rule_receives_loaded_valid_references_without_duplicate_queries(self):
        self.validator.validate([self.selection])
        self.repository.get_ministry.assert_called_once_with(7)
        self.repository.get_functions.assert_called_once_with([11])
        self.rule.validate.assert_called_once_with(self.ministry, (self.function,))
        self.repository.add.assert_not_called()

    def test_empty_memberships_do_not_run_rules_or_queries(self):
        self.validator.validate([])
        self.repository.get_ministry.assert_not_called()
        self.repository.get_functions.assert_not_called()
        self.rule.validate.assert_not_called()

    def test_unavailable_ministry_is_rejected_before_rules_or_function_lookup(self):
        for ministry in (
            None,
            Ministry(id=7, name="Inactive", status=MinistryStatus.INACTIVE),
            Ministry(id=7, name="Deleted", status=MinistryStatus.ACTIVE, deleted_at=datetime.now(timezone.utc)),
        ):
            with self.subTest(ministry=ministry):
                self.repository.get_ministry.return_value = ministry
                with self.assertRaisesRegex(RegistrationValidationError, "Selected ministry is not available"):
                    self.validator.validate([self.selection])
        self.repository.get_functions.assert_not_called()
        self.rule.validate.assert_not_called()

    def test_invalid_functions_are_rejected_before_extensions(self):
        for functions, message in (
            ([], "Selected function is not available"),
            ([Function(id=11, ministry_id=7, status=FunctionStatus.INACTIVE)], "Selected function is not available"),
            ([Function(id=11, ministry_id=7, status=FunctionStatus.ACTIVE, deleted_at=datetime.now(timezone.utc))],
             "Selected function is not available"),
            ([Function(id=11, ministry_id=8, status=FunctionStatus.ACTIVE)],
             "Selected function does not belong to the ministry"),
        ):
            with self.subTest(message=message, functions=functions):
                self.repository.get_functions.return_value = functions
                with self.assertRaisesRegex(RegistrationValidationError, message):
                    self.validator.validate([self.selection])
        self.rule.validate.assert_not_called()

    def test_multiple_rules_run_in_order_and_stop_on_first_rejection(self):
        first, second, third = (Mock(spec=MembershipRule) for _ in range(3))
        calls = []
        first.validate.side_effect = lambda *args: calls.append("first")

        def reject(*args):
            calls.append("second")
            raise RegistrationValidationError("Example policy rejected the selection.")

        second.validate.side_effect = reject
        validator = MembershipValidator(self.repository, (first, second, third))
        with self.assertRaisesRegex(RegistrationValidationError, "Example policy rejected"):
            validator.validate([self.selection])
        self.assertEqual(calls, ["first", "second"])
        third.validate.assert_not_called()

    def test_rules_are_applied_to_each_membership(self):
        other_ministry = Ministry(id=8, name="Hospitality", status=MinistryStatus.ACTIVE)
        self.repository.get_ministry.side_effect = [self.ministry, other_ministry]
        self.repository.get_functions.side_effect = [[self.function], []]
        self.validator.validate([self.selection, MembershipCreate(ministry_id=8)])
        self.assertEqual(self.rule.validate.call_count, 2)
        self.assertIs(self.rule.validate.call_args_list[0].args[0], self.ministry)
        self.rule.validate.assert_called_with(other_ministry, ())


class MembershipRuleExtensionContract:
    """Runs with the existing SQLite/PostgreSQL registration fixtures."""

    def enable_example_rule(self):
        rule = RequireFunctionForMinistry(self.ministries["Alabanza"])
        self.stack.enter_context(patch.dict(
            app.dependency_overrides, {get_membership_rules: lambda: (rule,)},
        ))

    def test_example_rule_rejects_then_accepts_target_ministry_through_real_workflow(self):
        self.enable_example_rule()
        self.assert_rejected(
            [{"ministry_id": self.ministries["Alabanza"]}],
            "Select at least one function for this ministry.",
        )
        response = self.client.post("/users/register", json={
            **self.payload, "memberships": [self.selection()],
        })
        self.assertEqual(response.status_code, 201, response.text)
        membership = response.json()["memberships"][0]
        self.assertEqual(membership["status"], "PENDING")
        self.assertEqual(membership["function_ids"], self.selection()["function_ids"])
        with self.session_factory() as db:
            stored = db.get(MinistryMember, membership["id"])
            self.assertEqual({item.function_id for item in stored.ministry_member_functions},
                             set(membership["function_ids"]))
            self.assertEqual(stored.ministry_member_roles, [])

    def test_example_rule_does_not_change_other_ministries(self):
        self.enable_example_rule()
        response = self.client.post("/users/register", json={
            **self.payload, "memberships": [self.selection("Hospitality")],
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["memberships"][0]["function_ids"], [])

    def test_example_rule_failure_on_second_membership_leaves_no_partial_user(self):
        self.enable_example_rule()
        self.assert_rejected([
            self.selection("Hospitality"), {"ministry_id": self.ministries["Alabanza"]},
        ], "Select at least one function for this ministry.")

    def test_example_rule_cannot_bypass_mandatory_function_validation(self):
        self.enable_example_rule()
        self.assert_rejected([{
            "ministry_id": self.ministries["Alabanza"], "function_ids": [self.functions["Sound"]],
        }], "Selected function does not belong to the ministry.")


if __name__ == "__main__":
    unittest.main()
