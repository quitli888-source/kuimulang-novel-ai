"""
番茄小说AI创作系统 V5 - 配置管理
支持从.env文件读取和写入，支持前端实时同步

P2-101: read_env 加 @lru_cache —— 100 次 LLM 调用每次拿 config → 100 次全文件读。
        write_env / delete_env / load_llm_config 等任何 .env 变更路径需失效缓存。
P2-102: write_env 保留原有 key 顺序 + 注释；新增 key 追加到末尾（不重排已有内容）。
"""
import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

def _resolve_base_dir() -> Path:
    """根据运行模式解析数据写入根目录。

    - 开发模式：`backend/core/config.py` → BASE_DIR = 项目根目录
    - frozen 模式：EXE 所在目录（_internal 是只读的，数据写到 EXE 旁）

    Returns:
        Path: 数据写入根目录
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent

def _resolve_prompt_dir() -> Path:
    """解析提示词目录（frozen 模式仍指向 _internal/prompts，只读）。

    P2-31: 允许 PROMPT_DIR 环境变量覆盖 —— 适合 monorepo 部署（backend 单独放在子目录）。
    启动时校验路径存在；不存在则降级到默认路径并打 warning。
    """
    override = os.environ.get('PROMPT_DIR', '').strip()
    if override:
        p = Path(override)
        if p.exists():
            return p
        # 不存在也不致命，只是 warning
        import warnings
        warnings.warn(f'PROMPT_DIR env {override} 不存在，回退默认路径')
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent / '_internal' / 'prompts'
    return Path(__file__).resolve().parent.parent.parent / 'prompts'
BASE_DIR = _resolve_base_dir()
DATA_DIR = BASE_DIR / 'data'
WORKS_DIR = DATA_DIR / 'works'
MEMORY_DIR = DATA_DIR / 'memory'
PROMPT_DIR = _resolve_prompt_dir()
ENV_FILE = BASE_DIR / '.env'
DATA_DIR.mkdir(parents=True, exist_ok=True)
WORKS_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

def _read_env_uncached(key: str, default: str='') -> str:
    if not ENV_FILE.exists():
        return default
    for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('#') or not line:
            continue
        if '=' in line:
            k, v = line.split('=', 1)
            if k.strip() == key:
                return v.strip()
    return default


@lru_cache(maxsize=256)
def read_env(key: str, default: str='') -> str:
    """P2-101: 缓存 .env 读取 —— 同一 key 多次调用只读一次文件。

    缓存失效由 write_env / delete_env / reload_env_cache 显式触发。
    """
    return _read_env_uncached(key, default)


def reload_env_cache():
    """P2-101: 显式清空缓存（外部直接修改 .env 后调用）。"""
    read_env.cache_clear()


def _env_int(name: str, default: int) -> int:
    """R1-C: 从进程环境变量读整数配置。

    read_env 只解析 .env 文件、不读 os.environ（R1-A 实证该语义缺口会让
    os.environ 设置被静默忽略），因此运行时覆盖类常量必须直接读 os.environ。
    """
    raw = os.environ.get(name, '')
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def get_task_max_tokens(task: str) -> int:
    """R2-5: 任务级 token 预算集中入口 —— 生产路径 max_tokens 的唯一来源。

    背景：step-5-preview 等推理模型的 reasoning token 计入 max_tokens，预算不足
    会被吃光导致 content 空返/短返（Round 1 实证：facts 6000 仍被吃尽 5 次，
    靠翻倍重试救回；style optimizer 10500 被吃光只剩 431 字）。
    此前预算散落四处口径不一（chunk 20000 / polish +500 / json 6000 / delta 2000），
    改一个常量就意外改写其他任务的失败模式。

    Args:
        task: 'chunk'（PartWriter 片段续写）/ 'polish'（StyleOptimizer 润色）/
              'json_facts'（facts 抽取 / story_delta）/ 'json_review'（三评审）

    Returns:
        该任务的 max_tokens（env KML_CHUNK_MAX_TOKENS / KML_POLISH_MAX_TOKENS /
        KML_JSON_MAX_TOKENS 可调）。
    """
    if task == 'chunk':
        # 片段首试一次给足（≈3.5 倍 CHUNK_WORDS），降低空返重试率；按量计费零成本
        return _env_int('KML_CHUNK_MAX_TOKENS', 20000)
    if task == 'polish':
        # 润色要重写全文：2 倍正文上限 + 2000 余量（5000 字 Part → 12000）
        return _env_int('KML_POLISH_MAX_TOKENS', PART_WORD_MAX * 2 + 2000)
    if task in ('json_facts', 'json_review'):
        # facts 输入含数千字原文、评审 JSON 也可能带 reasoning：默认 8000
        return _env_int('KML_JSON_MAX_TOKENS', 8000)
    raise ValueError(f'get_task_max_tokens: 未知 task {task!r}（可选 chunk/polish/json_facts/json_review）')


def get_json_max_tokens() -> int:
    """R1-C: JSON 类 LLM 调用（facts 抽取 / 三个 Review Agent）的显式 max_tokens 统一来源。

    step-5-preview 等推理模型的 reasoning token 计入 max_tokens，默认 4000 会被
    推理吃光导致 content 空返回（冒烟实证：facts 抽取 1800 → 3 次重试全败）。
    R2-5 起为 get_task_max_tokens('json_review') 的薄包装（默认 6000 → 8000），
    env KML_JSON_MAX_TOKENS 契约不变，既有调用方零改动。
    """
    return get_task_max_tokens('json_review')


def write_env(key: str, value: str):
    """P2-102: 写入或更新 .env —— 保留原有 key 顺序 + 注释；
    新增 key 追加到末尾（不重排已有内容）。
    P2-101: 写盘后失效 read_env 缓存。
    """
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding='utf-8').splitlines()
    new_lines = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#') or not stripped:
            # 注释 / 空行原样保留（保持用户写的顺序）
            new_lines.append(line)
            continue
        if '=' in stripped:
            k = stripped.split('=', 1)[0].strip()
            if k == key:
                new_lines.append(f'{key}={value}')
                found = True
                continue
        new_lines.append(line)
    if not found:
        # P2-102: 新增 key 追加到末尾（不强行按字母重排）
        new_lines.append(f'{key}={value}')
    ENV_FILE.write_text('\n'.join(new_lines), encoding='utf-8')
    reload_env_cache()  # P2-101: 失效缓存


def delete_env(key: str):
    """从.env中删除配置"""
    if not ENV_FILE.exists():
        return
    lines = [l for l in ENV_FILE.read_text(encoding='utf-8').splitlines() if not (l.strip().startswith(f'{key}=') and '=' in l)]
    ENV_FILE.write_text('\n'.join(lines), encoding='utf-8')
    reload_env_cache()  # P2-101: 失效缓存

@dataclass
class LLMConfig:
    """LLM配置（对应.env中的MINIMAX_API_KEY等）"""
    api_key: str = ''
    base_url: str = 'https://api.stepfun.com/step_plan/v1'
    model: str = 'step-3.7-flash'
    json_model: str = 'step-3.7-flash'

    @classmethod
    def from_env(cls) -> 'LLMConfig':
        return cls(api_key=read_env('STEP_API_KEY', '') or read_env('MINIMAX_API_KEY', ''), base_url=read_env('OPENAI_BASE_URL', 'https://api.stepfun.com/step_plan/v1'), model=read_env('OPENAI_MODEL', 'step-3.7-flash'), json_model=read_env('OPENAI_JSON_MODEL', 'step-3.7-flash'))

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class WritingTemplate:
    name: str
    target_words: int
    part_count: int
    part_word_min: int
    part_word_max: int
DEFAULT_TEMPLATES = [WritingTemplate('短篇', 10000, 3, 2500, 4000), WritingTemplate('中篇', 30000, 6, 4000, 6000), WritingTemplate('长篇', 50000, 10, 4000, 6000), WritingTemplate('超长篇', 100000, 20, 4000, 6000), WritingTemplate('自定义', 0, 0, 0, 0)]

@dataclass
class AgentModelConfig:
    model: str = 'MiniMax-Text-01'
    temperature: float = 0.7
    max_tokens: int = 4000

@dataclass
class AgentConfigs:
    """每个Agent的独立模型配置"""
    plot_planner: AgentModelConfig = field(default_factory=lambda: AgentModelConfig(model='MiniMax-Text-01', temperature=0.3, max_tokens=8000))
    part_writer: AgentModelConfig = field(default_factory=lambda: AgentModelConfig(model='MiniMax-Text-01', temperature=0.8, max_tokens=4000))
    style_optimizer: AgentModelConfig = field(default_factory=lambda: AgentModelConfig(model='MiniMax-Text-01', temperature=0.5, max_tokens=4000))
    emotion_review: AgentModelConfig = field(default_factory=lambda: AgentModelConfig(model='MiniMax-Text-01', temperature=0.3, max_tokens=2000))
    logic_review: AgentModelConfig = field(default_factory=lambda: AgentModelConfig(model='MiniMax-Text-01', temperature=0.3, max_tokens=2000))
    consistency_review: AgentModelConfig = field(default_factory=lambda: AgentModelConfig(model='MiniMax-Text-01', temperature=0.3, max_tokens=2000))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_env(cls) -> 'AgentConfigs':
        cfg = cls()
        for agent in ['plot_planner', 'part_writer', 'style_optimizer', 'emotion_review', 'logic_review', 'consistency_review']:
            model = read_env(f'MODEL_{agent.upper()}', '')
            temp = read_env(f'TEMP_{agent.upper()}', '')
            tokens = read_env(f'TOKENS_{agent.upper()}', '')
            agent_cfg = getattr(cfg, agent)
            if model:
                agent_cfg.model = model
            if temp:
                agent_cfg.temperature = float(temp)
            if tokens:
                agent_cfg.max_tokens = int(tokens)
        return cfg

@dataclass
class AppConfig:
    """前端可配置的应用配置"""
    template: WritingTemplate = field(default_factory=lambda: DEFAULT_TEMPLATES[0])
    confirm_mode: bool = True
    agent_configs: AgentConfigs = field(default_factory=AgentConfigs)
    llm: LLMConfig = field(default_factory=LLMConfig.from_env)
    _target_words: int = 10000
    _part_count: int = 3
    _part_word_min: int = 2500
    _part_word_max: int = 4000

    def __post_init__(self):
        template_name = read_env('TEMPLATE_NAME', '短篇')
        template_found = False
        for t in DEFAULT_TEMPLATES:
            if t.name == template_name:
                self.template = t
                template_found = True
                break
        if not template_found:
            self.template = DEFAULT_TEMPLATES[0]
        custom_target_words = read_env('CUSTOM_TARGET_WORDS', '')
        custom_part_count = read_env('CUSTOM_PART_COUNT', '')
        # R4-P1-x: int() 失败/part_count=0 此前直接 bubble 到启动路径（ZeroDivisionError /
        # ValueError），UI 配 0 或 .env 写非法值即无法启动。非法配置回退模板默认值。
        custom_words = custom_parts = None
        try:
            if custom_target_words:
                custom_words = int(custom_target_words)
            if custom_part_count:
                custom_parts = int(custom_part_count)
        except (TypeError, ValueError):
            _logger.info(f'[Config] 自定义字数/Part数配置非法: words={custom_target_words!r}, parts={custom_part_count!r}，回退模板默认值')
        if custom_words and custom_parts and custom_words > 0 and custom_parts > 0:
            self._target_words = custom_words
            self._part_count = custom_parts
            self._part_word_min = max(1000, self._target_words // self._part_count // 2)
            self._part_word_max = min(10000, self._target_words // self._part_count * 2)
        else:
            self._target_words = self.template.target_words
            self._part_count = self.template.part_count
            self._part_word_min = self.template.part_word_min
            self._part_word_max = self.template.part_word_max
        _logger.info(f'AppConfig初始化: template={self.template.name}, target_words={self._target_words}, part_count={self._part_count}')

    def apply_template(self, template: WritingTemplate, custom_target_words=None, custom_part_count=None):
        self.template = template
        if template.name == '自定义':
            if custom_target_words and custom_part_count:
                self._target_words = custom_target_words
                self._part_count = custom_part_count
                self._part_word_min = max(1000, self._target_words // self._part_count // 2)
                self._part_word_max = min(10000, self._target_words // self._part_count * 2)
            else:
                custom_target_words = read_env('CUSTOM_TARGET_WORDS', '')
                custom_part_count = read_env('CUSTOM_PART_COUNT', '')
                if custom_target_words and custom_part_count:
                    self._target_words = int(custom_target_words)
                    self._part_count = int(custom_part_count)
                    self._part_word_min = max(1000, self._target_words // self._part_count // 2)
                    self._part_word_max = min(10000, self._target_words // self._part_count * 2)
            _logger.info(f'应用自定义模板: target_words={self._target_words}, part_count={self._part_count}')
        elif template.target_words > 0:
            self._target_words = template.target_words
            self._part_count = template.part_count
            self._part_word_min = template.part_word_min
            self._part_word_max = template.part_word_max
            _logger.info(f'应用预设模板 {template.name}: target_words={self._target_words}, part_count={self._part_count}')

    @property
    def target_word_count(self) -> int:
        return self._target_words

    @property
    def part_count(self) -> int:
        return self._part_count

    @property
    def part_word_min(self) -> int:
        return self._part_word_min

    @property
    def part_word_max(self) -> int:
        return self._part_word_max
_TARGET_WORDS = 10000
_PART_COUNT = 3
_PART_WORD_MIN = 2500
_PART_WORD_MAX = 4000

def _sync_from_app():
    global _TARGET_WORDS, _PART_COUNT, _PART_WORD_MIN, _PART_WORD_MAX
    cfg = get_app_config()
    _TARGET_WORDS = cfg._target_words
    _PART_COUNT = cfg._part_count
    _PART_WORD_MIN = cfg._part_word_min
    _PART_WORD_MAX = cfg._part_word_max
PART_COUNT = _PART_COUNT
PART_WORD_MIN = _PART_WORD_MIN
PART_WORD_MAX = _PART_WORD_MAX
TARGET_WORD_COUNT = _TARGET_WORDS
MEMORY_DIR_PATH = MEMORY_DIR
WORKS_DIR_PATH = WORKS_DIR

def reload_config():
    """重新加载配置（V4代码需要在使用前调用此函数）"""
    _sync_from_app()
    global PART_COUNT, PART_WORD_MIN, PART_WORD_MAX, TARGET_WORD_COUNT
    PART_COUNT = _PART_COUNT
    PART_WORD_MIN = _PART_WORD_MIN
    PART_WORD_MAX = _PART_WORD_MAX
    TARGET_WORD_COUNT = _TARGET_WORDS
_app_config: Optional[AppConfig] = None

def init_app_config():
    global _app_config
    _app_config = AppConfig()

def get_app_config() -> AppConfig:
    global _app_config
    if _app_config is None:
        init_app_config()
    return _app_config
from .llm_providers import PRESET_PROVIDERS, get_provider_from_id, load_provider_api_key, ActiveLLMConfig
# R22: 不导入 core.logger（避免循环：logger → config.DATA_DIR → config）
# 用 stdlib logging 直接拿 logger
import logging as _logging
_logger = _logging.getLogger(__name__)
_llm_config: ActiveLLMConfig = None
_LLM_CONFIG_FILE = DATA_DIR / 'llm_config.json'
_LLM_CONFIG_MIGRATION_FLAG = DATA_DIR / '.llm_config_migrated'

def _dataclass_defaults() -> dict:
    """获取 ActiveLLMConfig 的所有 dataclass 默认值（含 default_factory）。
    R7-P0-1: 过滤掉 dataclasses._MISSING_TYPE 标记（不可 JSON 序列化）。
    """
    import dataclasses
    MISSING = dataclasses.MISSING
    defaults = {}
    for f_name, f_obj in ActiveLLMConfig.__dataclass_fields__.items():
        try:
            if f_obj.default is not MISSING and (not isinstance(f_obj.default, type(MISSING))):
                defaults[f_name] = f_obj.default
                continue
        except Exception:
            _logger.debug('app: silent except (P2-19)', exc_info=True)
        if f_obj.default_factory is not MISSING and f_obj.default_factory is not None:
            try:
                defaults[f_name] = f_obj.default_factory()
            except Exception:
                defaults[f_name] = None
    return defaults

def migrate_llm_config() -> bool:
    """R7-P0-1: 启动时 migrate data/llm_config.json。

    策略：
      - 用 dataclass 默认值兜底补齐缺失字段（避免运行时 fallback 丢用户意图）
      - 检测旧值 "minimax" 等已知遗留 provider，且没有用户自定义 base_url
        → 自动迁移到 dataclass 默认（目前为 "step"），并打 log
      - 写回 json（保留用户自定义的 agent_providers / agent_temperatures 等）
      - 写 .llm_config_migrated flag，下次启动不再重复迁移（除非 json 又被改回旧值）

    Returns:
        bool: 是否发生了迁移（写盘 + 改 active_provider_id）。
    """
    if not _LLM_CONFIG_FILE.exists():
        return False
    try:
        raw = _LLM_CONFIG_FILE.read_text(encoding='utf-8')
        data = json.loads(raw)
    except Exception as e:
        _logger.info(f'[Config] migrate_llm_config: 解析 json 失败，跳过迁移: {e}')
        return False
    if not isinstance(data, dict):
        return False
    defaults = _dataclass_defaults()
    merged = {**defaults, **data}
    merged = {k: v for k, v in merged.items() if k in ActiveLLMConfig.__dataclass_fields__}
    migrated = False
    legacy_providers = {'minimax', 'MiniMax'}
    active_provider = merged.get('active_provider_id', '')
    if active_provider in legacy_providers:
        raw_ap = data.get('agent_providers') or {}
        user_customized = False
        if isinstance(raw_ap, dict) and raw_ap:
            user_customized = any((v not in legacy_providers for v in raw_ap.values()))
        if not user_customized:
            old = active_provider
            merged['active_provider_id'] = defaults.get('active_provider_id', 'step')
            _logger.info(f"[Config] migrated active_provider_id: {old} -> {merged['active_provider_id']}")
            migrated = True
    if migrated:
        try:
            _LLM_CONFIG_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')
            try:
                _LLM_CONFIG_MIGRATION_FLAG.touch()
            except Exception:
                _logger.debug('app: silent except (P2-19)', exc_info=True)
        except Exception as e:
            _logger.info(f'[Config] migrate_llm_config: 写盘失败（不影响主流程）: {e}')
            return False
    global _llm_config
    _llm_config = ActiveLLMConfig.from_dict(merged)
    return migrated

def load_llm_config() -> ActiveLLMConfig:
    """从磁盘加载LLM供应商配置（R7-P0-1: 顶部先调 migrate 兜底旧 json）"""
    global _llm_config
    if _llm_config is not None:
        return _llm_config
    if _LLM_CONFIG_FILE.exists() and (not _LLM_CONFIG_MIGRATION_FLAG.exists()):
        try:
            migrate_llm_config()
        except Exception as e:
            _logger.info(f'[Config] migrate_llm_config 异常（不影响主流程）: {e}')
    if _LLM_CONFIG_FILE.exists():
        try:
            data = json.loads(_LLM_CONFIG_FILE.read_text(encoding='utf-8'))
            defaults = _dataclass_defaults()
            merged = {**defaults, **data}
            merged = {k: v for k, v in merged.items() if k in ActiveLLMConfig.__dataclass_fields__}
            _llm_config = ActiveLLMConfig.from_dict(merged)
        except Exception:
            _llm_config = ActiveLLMConfig()
    else:
        _llm_config = ActiveLLMConfig()
    return _llm_config

def save_llm_config(cfg: ActiveLLMConfig):
    """保存LLM供应商配置到磁盘"""
    _LLM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LLM_CONFIG_FILE.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
    global _llm_config
    _llm_config = cfg

def get_llm_config_for_agent(agent_name: str) -> LLMConfig:
    """
    根据当前激活的供应商配置，返回指定Agent应使用的LLMConfig
    支持全局模式和per-agent模式
    """
    llm_cfg = load_llm_config()
    if llm_cfg.per_agent_enabled:
        provider_id = llm_cfg.agent_providers.get(agent_name, llm_cfg.active_provider_id)
    else:
        provider_id = llm_cfg.active_provider_id
    provider = get_provider_from_id(provider_id)
    if provider is None:
        _logger.info(f"[Config] 警告: 供应商 '{provider_id}' 未找到，使用默认供应商 minimax")
        provider = get_provider_from_id('minimax')
    loaded = load_provider_api_key(provider, read_env)
    if not loaded.api_key:
        _logger.info(f"[Config] 错误: 供应商 '{provider_id}' 的 API Key 未配置!")
        _logger.info(f'[Config] 请在 .env 文件中设置 {loaded.api_key_name}')
    else:
        key_preview = loaded.api_key[:10] + '...' if len(loaded.api_key) > 10 else '[空]'
        _logger.info(f"[Config] 供应商 '{provider_id}' API Key 已配置: {key_preview}")
    return LLMConfig(api_key=loaded.api_key, base_url=loaded.base_url, model=loaded.model, json_model=loaded.json_model)

def get_all_providers() -> list:
    """返回所有预设供应商（带用户已填写的API Key状态）"""
    result = []
    for p in PRESET_PROVIDERS:
        loaded = load_provider_api_key(p, read_env)
        result.append({'id': loaded.id, 'name': loaded.name, 'has_api_key': bool(loaded.api_key and loaded.api_key != ''), 'base_url': loaded.base_url, 'model': loaded.model, 'json_model': loaded.json_model})
    return result

def get_llm_config() -> LLMConfig:
    """返回当前激活供应商的LLM配置（用于单供应商场景）"""
    return get_llm_config_for_agent('part_writer')