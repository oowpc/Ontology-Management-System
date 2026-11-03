"""OMS 统一校验：入口、对象适配器、结果留痕。"""
from .service import revalidate, run
from .types import ValidationResult, issue

__all__ = ["ValidationResult", "issue", "run", "revalidate"]
