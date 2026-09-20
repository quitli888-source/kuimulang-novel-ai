"""
番茄小说AI创作系统 V5 - AI辅助改写服务
支持选中文字 → 润色/扩写/缩写
"""
import asyncio
from core.config import get_app_config, read_env
from core.llm_client import get_client


class RewriteService:
    """AI改写服务 - 封装LLM调用"""

    SYSTEM_PROMPTS = {
        "polish": """你是一位专业的中文小说文字润色师。你的任务是对给定的文本进行润色。
要求：
1. 保持原文的核心意思和风格
2. 让文字更流畅、更有文学质感
3. 不要添加解释说明，直接输出改写后的文本
4. 不要改变原文的字数（最多±5%）
5. 禁止添加任何注释或标注""",

        "expand": """你是一位专业的中文小说作家。你的任务是对给定的文本进行扩写。
要求：
1. 保持原文的核心意思和情节走向
2. 增加细节描写、对话、心理活动
3. 扩写后的文字要自然流畅，不能有堆砌感
4. 扩写幅度控制在原文字数的30%-50%
5. 不要添加任何注释或标注，直接输出扩写后的正文""",

        "summarize": """你是一位专业的中文小说文字编辑。你的任务是对给定的文本进行缩写。
要求：
1. 保持原文的核心意思和关键情节
2. 删除冗余的描写和不必要的过渡
3. 缩写后保留原文约50%-60%的字数
4. 保持原文的文风和情感基调
5. 不要添加任何注释或标注，直接输出缩写后的正文""",
    }

    # R4-P2-x: 单次改写输入上限 —— 此前无限制，粘贴 20 万字符会产生
    # max_tokens=40万 的请求（provider 400 或巨额计费）。
    MAX_INPUT_CHARS = 20000
    VALID_MODES = ('polish', 'expand', 'summarize')

    async def rewrite(self, text: str, mode: str, context: str = "") -> dict:
        """
        执行AI改写
        mode: "polish" | "expand" | "summarize"
        """
        cfg = get_app_config()
        # R4-P2-x: mode 白名单校验 —— 此前任意值静默回落到 polish，调用方无从感知
        if mode not in self.VALID_MODES:
            return {
                "original": text,
                "rewritten": text,
                "mode": mode,
                "error": f"不支持的改写模式: {mode}（可选: {', '.join(self.VALID_MODES)}）",
            }
        if len(text) > self.MAX_INPUT_CHARS:
            return {
                "original": text,
                "rewritten": text,
                "mode": mode,
                "error": f"输入过长（{len(text)} 字符，上限 {self.MAX_INPUT_CHARS}）",
            }
        system = self.SYSTEM_PROMPTS[mode]

        user_prompt = f"原文：\n{text}"
        if context:
            user_prompt = f"上下文：\n{context[:1000]}\n\n原文：\n{text}"

        try:
            client = get_client()
            model = cfg.llm.model or read_env("OPENAI_MODEL", "MiniMax-Text-01")
            # R4-P2-x: 同步 SDK 调用放线程池 —— 此前 30-120s 的改写请求直接阻塞
            # 整个 asyncio 事件循环，SSE 心跳/其它请求全部排队。
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.5,
                max_tokens=min(len(text) * 2 + 500, 16000),
            )
            rewritten = (response.choices[0].message.content or "").strip()
        except Exception as e:
            return {
                "original": text,
                "rewritten": text,
                "mode": mode,
                "error": str(e),
            }

        return {
            "original": text,
            "rewritten": rewritten,
            "mode": mode,
            "original_len": len(text),
            "rewritten_len": len(rewritten),
        }
