from pydantic import AwareDatetime, BaseModel


class CalendarRehearsal(BaseModel):
    id: int
    title: str
    ministry_id: int
    ministry_name: str
    start: AwareDatetime
    end: AwareDatetime
