"""Pydantic schemas shared across the API and internal services."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Role(StrEnum):
    admin = "admin"
    host = "host"
    operator = "operator"


class EventType(StrEnum):
    chat = "chat"
    gift = "gift"
    user_enter = "user_enter"
    like = "like"
    follow = "follow"


class ActionType(StrEnum):
    auto_reply = "auto_reply"
    welcome = "welcome"
    pin = "pin"
    alert = "alert"
    escalate = "escalate"


class ActionStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    executed = "executed"


class Intent(StrEnum):
    question = "question"
    praise = "praise"
    complaint = "complaint"
    price_inquiry = "price_inquiry"
    order = "order"
    spam = "spam"
    chitchat = "chitchat"


class Sentiment(StrEnum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"


class UserPublic(BaseModel):
    id: int
    username: str
    display_name: str
    role: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class LiveEvent(BaseModel):
    id: int | None = None
    session_id: str
    type: str
    payload: dict[str, Any]
    ts: float


class LiveAction(BaseModel):
    id: int | None = None
    session_id: str
    type: str
    content: str
    trigger_event_id: int | None = None
    status: str = "pending"
    ts: float


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=40, pattern=r"^[a-zA-Z0-9_]+$")
    display_name: str = Field(default="", max_length=40)
    password: str = Field(min_length=6, max_length=64)
    role: str = Field(default="operator")


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=40)
    role: str | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=6, max_length=64)


class PersonaUpdate(BaseModel):
    persona_name: str | None = Field(default=None, max_length=40)
    persona_personality: str | None = Field(default=None, max_length=200)
    persona_style: str | None = Field(default=None, max_length=200)
    persona_knowledge_scope: str | None = Field(default=None, max_length=200)
    persona_forbidden_topics: str | None = Field(default=None, max_length=200)


class SessionStartRequest(BaseModel):
    title: str = Field(default="直播间", min_length=1, max_length=80)
    event_rate: float = Field(default=2.0, ge=0.5, le=20.0)
    # Event source: "mock" (built-in), "douyin" (direct connect, no tools),
    # "douyin_grab" (DouyinBarrageGrab local WS), "wxlive" (webhook).
    platform: str = Field(
        default="mock", pattern="^(mock|douyin|douyin_grab|wxlive)$"
    )
    room_id: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def _validate_douyin_room(self) -> "SessionStartRequest":
        if self.platform != "douyin":
            return self
        raw = self.room_id.strip()
        m = re.search(r"live\.douyin\.com/([A-Za-z0-9]+)", raw)
        if m and re.fullmatch(r"[A-Za-z0-9]{6,24}", m.group(1)):
            return self
        # 抖音分享短链/整段分享文案，web_rid 由适配器联网解析
        if re.search(r"v\.douyin\.com/[A-Za-z0-9]+", raw):
            return self
        if re.fullmatch(r"[A-Za-z0-9]{6,24}", raw):
            return self
        raise ValueError(
            "抖音直播需填写直播间号（链接尾号）、直播间链接或抖音分享短链"
        )


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    product: str = Field(min_length=1, max_length=80)
    category: str = Field(default="general", max_length=40)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class KnowledgeOut(KnowledgeCreate):
    id: int
    created_at: float


class KnowledgeOrganizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    product: str = Field(default="", max_length=80)
    category: str = Field(default="", max_length=40)


class KnowledgeEnrichRequest(BaseModel):
    title: str = Field(default="", max_length=120)
    product: str = Field(default="", max_length=80)
    category: str = Field(default="", max_length=40)
    content: str = Field(min_length=1)


class KnowledgeOrganizeOut(BaseModel):
    items: list[KnowledgeCreate]


class KnowledgeEnrichOut(BaseModel):
    item: KnowledgeCreate


class AssistRequest(BaseModel):
    session_id: str | None = None
    query: str = Field(min_length=1, max_length=500)
    context: list[dict[str, Any]] = Field(default_factory=list)


class ContentRequest(BaseModel):
    kind: str = Field(default="opening", max_length=40)
    topic: str = Field(default="", max_length=120)
    style: str = Field(default="自然亲和", max_length=40)
    context: list[dict[str, Any]] = Field(default_factory=list)


class RulesUpdate(BaseModel):
    gift_alert_threshold: int | None = None
    gift_thank_threshold: int | None = None
    vip_welcome_level: int | None = None
    enabled_actions: list[str] | None = None
    sensitive_words: list[str] | None = None


class ScriptCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1)
    type: str = Field(default="text", pattern="^(text|audio)$")
    tags: list[str] = Field(default_factory=list)


class ScriptUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = None
    type: str | None = Field(default=None, pattern="^(text|audio)$")
    tags: list[str] | None = None
    enabled: bool | None = None


class ActionReviewRequest(BaseModel):
    status: ActionStatus
