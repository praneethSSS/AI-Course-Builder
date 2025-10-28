from pydantic import BaseModel, Field
from typing import List, Optional

class Lesson(BaseModel):
    title: str
    content: str

class Course(BaseModel):
    title: str
    description: str
    category: str
    difficulty: str
    lessons: Optional[List[Lesson]] = []
