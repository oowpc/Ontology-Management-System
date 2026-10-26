"""环境配置：全部可用环境变量覆盖（见 .env.example）。"""
from __future__ import annotations

import json
import os


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _json_obj(name: str, default: dict) -> dict:
    """JSON 环境变量：整体并入默认值（只覆盖指定键），非法 JSON 回退默认。"""
    try:
        return {**default, **json.loads(os.environ.get(name, "{}"))}
    except (TypeError, ValueError):
        return default


# KE 上传 CSV 的默认列名映射（规范字段 → 表头；可选列：article 篇编号 / content KE内容）
DEFAULT_KE_COLUMN_MAP = {
    "id": "题目ID", "domain": "领域", "summary": "题目摘要", "status": "标注状态",
    "person": "标注人", "created": "创建时间", "started": "开始时间", "finished": "完成时间",
    "review": "复核结论", "tags": "问题标签", "article": "篇编号", "content": "KE内容",
}

# KE 状态词表：语义键 → CSV 中允许的写法（首个为规范值；内部统一用规范值计算）
DEFAULT_KE_STATUS_MAP = {
    "todo": ["待标注"], "doing": ["标注中"], "review": ["待复核"],
    "done": ["已完成"], "returned": ["已退回"],
}

# GOLD 流程词表（阶段 [key, 中文名] / 泳道时段）；CHECK 约束在首次建库时定型
DEFAULT_GOLD_STAGES = [["co_annotation", "CO 标注与对齐"], ["co_merge", "大本体整理"],
                       ["ke_annotation", "KE 标注与对齐"], ["done", "已完成"]]
DEFAULT_GOLD_SLOTS = ["上午", "下午"]


def _json_list(name: str, default: list) -> list:
    try:
        value = json.loads(os.environ.get(name, ""))
        return value if isinstance(value, list) and value else default
    except (TypeError, ValueError):
        return default


class Settings:
    db_url: str = os.environ.get("DB_URL", "sqlite:///data/ontology.db")
    secret_key: str = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    seed_demo: bool = os.environ.get("SEED_DEMO", "1") == "1"

    similarity_block_threshold: float = _float("SIMILARITY_BLOCK_THRESHOLD", 0.95)
    similarity_suggest_threshold: float = _float("SIMILARITY_SUGGEST_THRESHOLD", 0.75)

    health_operator_max: int = _int("HEALTH_OPERATOR_MAX", 15)
    health_cron: str = os.environ.get("HEALTH_CRON", "30 3 * * *")
    root_concepts: list[str] = [
        x.strip()
        for x in os.environ.get("ROOT_CONCEPTS", "机构,行政区划,人,职务,民族,职责").split(",")
        if x.strip()
    ]

    # M7 · KE 标注进度
    ke_deadline: str = os.environ.get("KE_DEADLINE", "2026-11-15")  # 理想线终点 / 预计完成日基准
    seed_ke: bool = os.environ.get("SEED_KE", "1") == "1"           # 演示：向「一组」灌入示例标注数据
    # 通用性：其他团队的标注表头不同 → KE_COLUMN_MAP='{"id":"题目编号"}' 覆盖单项即可
    ke_column_map: dict = _json_obj("KE_COLUMN_MAP", DEFAULT_KE_COLUMN_MAP)
    # 通用性：CSV 里的状态写法不同 → KE_STATUS_MAP='{"done":["done","完成"]}' 追加写法
    ke_status_map: dict = _json_obj("KE_STATUS_MAP", DEFAULT_KE_STATUS_MAP)

    # M8 · GOLDKE（《数据标注执行方案》口径，词表可按工况改）
    gold_baseline: int = _int("GOLD_BASELINE", 400)      # 存量 KE 条数
    gold_target_total: int = _int("GOLD_TARGET_TOTAL", 800)   # 累计目标
    gold_target_new: int = _int("GOLD_TARGET_NEW", 400)       # 本期新增目标
    gold_stages: list = _json_list("GOLD_STAGES", DEFAULT_GOLD_STAGES)  # 篇级阶段流水线
    gold_slots: list = _json_list("GOLD_SLOTS", DEFAULT_GOLD_SLOTS)     # 泳道时段
    # M20：定稿（→gold）强校验 KE 内容（结构错误阻断；词表 warning 不阻断）。0=关闭门槛
    gold_finalize_validate: bool = os.environ.get("GOLD_FINALIZE_VALIDATE", "1") == "1"

    # LLM（OpenAI 兼容端点；留空则相关功能优雅降级）
    llm_base_url: str = os.environ.get("LLM_BASE_URL", "")
    llm_api_key: str = os.environ.get("LLM_API_KEY", "")
    llm_model: str = os.environ.get("LLM_MODEL", "")
    llm_timeout: float = _float("LLM_TIMEOUT", 45)
    llm_max_retries: int = _int("LLM_MAX_RETRIES", 2)   # M23：429/5xx/网络错误退避重试次数


settings = Settings()
