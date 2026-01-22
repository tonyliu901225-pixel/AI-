"""
Analyst Skill

核心能力：
- 加载 config/analyst_rules.yaml
- 配置 Gemini API（GEMINI_API_KEY）
- 批量拉取待分析笔记 -> 调用 Gemini -> JSON 解析 -> 入库 -> 标记完成

依赖：
- google.generativeai
- core.db_manager.DBManager
- core.config_loader.ConfigManager
"""

from __future__ import annotations

import json
import os
import time
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image
import google.generativeai as genai

from core.db_manager import DBManager
from core.config_loader import ConfigManager


class AnalystSkill:
    def __init__(self, db: Optional[DBManager] = None, config: Optional[ConfigManager] = None):
        # 1) 加载配置
        self.config = config or ConfigManager()
        self.rules: Dict[str, Any] = self.config.load_analyst_rules()

        model_cfg = (self.rules.get("model_config") or {})
        self.model_name: str = str(model_cfg.get("model_name", "gemini-1.5-pro-latest"))
        self.temperature: float = float(model_cfg.get("temperature", 0.4))
        self.max_output_tokens: int = int(model_cfg.get("max_output_tokens", 2048))
        self.batch_size: int = int(model_cfg.get("batch_size", 1))
        self.system_prompt: str = str(self.rules.get("system_prompt", "")).strip()
        # Gemini 请求超时（秒），避免“无声卡住”
        self.timeout_seconds: int = int(model_cfg.get("timeout_seconds", 60))
        # 传输协议：默认强制 REST（避免 gRPC 在部分网络/IPv6 环境下卡死重试）
        # 可通过 analyst_rules.yaml: model_config.transport 或环境变量 GEMINI_TRANSPORT 覆盖
        self.transport: str = str(
            os.getenv("GEMINI_TRANSPORT") or model_cfg.get("transport") or "rest"
        ).strip().lower()

        # 2) 配置 Gemini API
        api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("未检测到 GEMINI_API_KEY，请先在环境变量中配置 Gemini API Key。")
        # ✅ 默认走 REST，规避 gRPC 连接失败导致的长时间阻塞/重试
        # 允许值通常为 "rest" 或 "grpc"
        if self.transport in ("rest", "grpc"):
            genai.configure(api_key=api_key, transport=self.transport)
        else:
            genai.configure(api_key=api_key)
        print(f"🔧 Gemini transport={self.transport}")

        # 3) 初始化模型与数据库
        self.model = genai.GenerativeModel(self.model_name)
        self.db = db or DBManager()
        # 网络预检失败不阻塞初始化（允许用户在 UI 中配置代理后重试）
        try:
            self._preflight_network()
        except Exception as e:
            print(f"⚠️  网络预检失败（非阻塞）：{e}")
            print("💡 提示：如在受限网络环境，请在 Streamlit 侧边栏配置 HTTPS_PROXY/HTTP_PROXY 后重试。")

    def _list_model_names(self, limit: int = 50) -> List[str]:
        """
        列出当前 Key/Endpoint 下可用的模型名（用于诊断 404 model not found）。
        """
        names: List[str] = []
        try:
            for m in genai.list_models():
                n = getattr(m, "name", "") or ""
                if n:
                    # 返回形如 "models/gemini-1.5-pro"；我们要的是后半段
                    if n.startswith("models/"):
                        n = n.split("/", 1)[1]
                    names.append(n)
                if len(names) >= limit:
                    break
        except Exception:
            # 列表失败不影响主流程
            return []
        return names

    def _pick_generatecontent_model(self) -> Optional[str]:
        """
        选一个明确支持 generateContent 的模型名（优先 flash/pro，再兜底第一个可用）。
        """
        try:
            candidates: List[str] = []
            for m in genai.list_models():
                name = getattr(m, "name", "") or ""
                methods = getattr(m, "supported_generation_methods", None) or []
                if "generateContent" not in methods:
                    continue
                if name.startswith("models/"):
                    name = name.split("/", 1)[1]
                if name:
                    candidates.append(name)

            if not candidates:
                return None

            # 常见优先级（新版本可能是 2.0 系列；旧版本可能是 1.5 系列）
            preferred_keywords = [
                "2.0-flash",
                "2.0-pro",
                "1.5-flash",
                "1.5-pro",
                "flash",
                "pro",
            ]
            for kw in preferred_keywords:
                for c in candidates:
                    if kw in c:
                        return c
            return candidates[0]
        except Exception:
            return None

    def _maybe_reinit_model_for_404(self, err: Exception) -> bool:
        """
        若是 404 model not found，则尝试回退模型并重建 GenerativeModel。
        Returns: 是否已完成回退并重建
        """
        msg = str(err)
        if "404" not in msg or "model" not in msg or "not found" not in msg:
            return False

        # 直接从 ListModels 里挑一个支持 generateContent 的模型
        picked = self._pick_generatecontent_model()
        if not picked or picked == self.model_name:
            # 打印可用模型，方便人工判断
            available = self._list_model_names(limit=60)
            if available:
                print("📋 当前 Key 可用模型（截断展示）:", ", ".join(available[:30]))
            return False

        print(f"⚠️ 模型不可用：{self.model_name}，自动切换为可用模型：{picked}")
        self.model_name = picked
        self.model = genai.GenerativeModel(self.model_name)
        return True

    def _preflight_network(self) -> None:
        """
        预检网络连通性：避免在 generate_content 内部“长时间重试看起来像卡死”。
        说明：
        - Gemini API（google.generativeai）最终会访问 Google 端点（generativelanguage.googleapis.com:443）。
        - 若当前网络/DNS 阻断（常见于公司网络/运营商 DNS 劫持/无代理），应尽早给出明确提示。
        """
        host = "generativelanguage.googleapis.com"
        port = 443
        timeout = 3.0

        # 打印代理环境（如用户使用 Clash/系统代理，通常会设置这些）
        proxy = (
            os.getenv("HTTPS_PROXY")
            or os.getenv("https_proxy")
            or os.getenv("HTTP_PROXY")
            or os.getenv("http_proxy")
        )
        if proxy:
            # 注意：本预检使用的是“直连 TCP 探测”，会绕过 HTTP/SOCKS 代理，
            # 在“必须走代理才能访问 Google”的网络环境下会误判失败。
            # 因此检测到代理时直接跳过预检，让 SDK 走代理完成请求。
            print(f"🧩 检测到代理环境变量，跳过直连预检: {proxy}")
            return

        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            ips = sorted({i[4][0] for i in infos})
            print(f"🌐 预检 DNS: {host} -> {ips[:8]}{'...' if len(ips) > 8 else ''}")
        except Exception as e:
            raise RuntimeError(f"网络预检失败：DNS 解析 {host} 失败：{e}") from e

        # 尝试逐个 IP 做 TCP connect（优先 IPv4）
        ipv4 = [ip for ip in ips if ":" not in ip]
        ipv6 = [ip for ip in ips if ":" in ip]
        candidates = ipv4 + ipv6
        last_err: Optional[Exception] = None

        for ip in candidates[:6]:
            try:
                s = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(timeout)
                s.connect((ip, port))
                s.close()
                print(f"✅ 预检连通: tcp://{ip}:{port}")
                return
            except Exception as e:
                last_err = e

        raise RuntimeError(
            "网络预检失败：无法连接 Gemini 端点 generativelanguage.googleapis.com:443。\n"
            f"最后错误: {last_err}\n"
            "建议：\n"
            "- 确认当前网络可访问 Google（尝试 `curl -I https://www.google.com`）\n"
            "- 如在受限网络环境，请配置代理（设置 HTTPS_PROXY/http_proxy），或使用 VPN\n"
            "- 如 DNS 异常/劫持，尝试切换 DNS（如 8.8.8.8 / 1.1.1.1）"
        )

    def analyze_pending_notes(self) -> None:
        """
        主循环：拉取待分析笔记并逐条处理
        """
        notes = self.db.get_pending_notes(limit=self.batch_size)
        if not notes:
            print("✅ 当前没有待分析笔记")
            return

        for note in notes:
            note_id = str(note.get("id", "")).strip()
            try:
                title = str(note.get("title", "") or "")
                content = str(note.get("content", "") or "")
                image_paths = self._extract_image_paths(note)

                print(f"📝 准备分析笔记 [{note_id}]（图片数={len(image_paths)}）")
                prompt = self._build_prompt(title=title, content=content)
                parts: List[Any] = [prompt]
                parts.extend(self._load_images(image_paths))

                print(f"🌐 调用 Gemini 中...（note_id={note_id}, model={self.model_name}, timeout={self.timeout_seconds}s）")
                t0 = time.time()
                resp = self.model.generate_content(
                    parts,
                    generation_config={
                        "temperature": self.temperature,
                        "max_output_tokens": self.max_output_tokens,
                    },
                    request_options={"timeout": self.timeout_seconds},
                )
                dt = time.time() - t0
                print(f"📥 Gemini 返回完成（note_id={note_id},耗时={dt:.1f}s）")

                raw_text = (getattr(resp, "text", None) or "").strip()
                if not raw_text:
                    raise RuntimeError(f"Gemini 返回空文本：note_id={note_id}")

                try:
                    result_dict = json.loads(raw_text)
                except Exception:
                    # 尝试截取第一个 JSON 对象（防止模型前后夹带说明）
                    start = raw_text.find("{")
                    end = raw_text.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        result_dict = json.loads(raw_text[start : end + 1])
                    else:
                        raise

                if not isinstance(result_dict, dict):
                    raise ValueError(f"解析结果不是 JSON 对象(dict)：{type(result_dict).__name__}")

                # ✅ 标签合并逻辑：将 source_keyword 作为硬标签与 AI tags 合并
                # 最终格式：所有标签都带 #，用空格分隔，例如：#办公好物 #总助 #职场
                source_keyword = str(note.get("source_keyword", "") or "").strip()
                hard_tag = source_keyword.lstrip("#").strip()
                ai_tags_raw = str(result_dict.get("tags", "") or "").strip()
                parts = []
                if ai_tags_raw:
                    # 支持逗号/中文逗号/空格/换行分隔
                    ai_tags_norm = (
                        ai_tags_raw.replace("，", ",")
                        .replace("\n", " ")
                        .replace("\t", " ")
                        .replace("  ", " ")
                    )
                    for chunk in ai_tags_norm.replace(",", " ").split():
                        t = chunk.strip().lstrip("#").strip()
                        if t:
                            parts.append(t)
                if hard_tag:
                    parts.append(hard_tag)

                # 去重保持顺序 + 统一加 #
                seen = set()
                merged = []
                for t in parts:
                    if t not in seen:
                        seen.add(t)
                        merged.append(f"#{t}")
                result_dict["tags"] = " ".join(merged)

                # 入库 + 标记完成
                print(f"💾 写入数据库（note_id={note_id}）")
                if not self.db.save_analysis(note_id, result_dict):
                    raise RuntimeError(f"保存分析结果失败：note_id={note_id}")
                if not self.db.mark_as_analyzed(note_id):
                    raise RuntimeError(f"标记已分析失败：note_id={note_id}")

                print(f"✅ 笔记 [{note_id}] 分析完成")
            except Exception as e:
                # 如果是模型名不支持（404），尝试自动回退一次再重试
                if self._maybe_reinit_model_for_404(e):
                    try:
                        print(f"🔁 使用回退模型重试（note_id={note_id}, model={self.model_name}）")
                        t0 = time.time()
                        resp = self.model.generate_content(
                            parts,
                            generation_config={
                                "temperature": self.temperature,
                                "max_output_tokens": self.max_output_tokens,
                            },
                            request_options={"timeout": self.timeout_seconds},
                        )
                        dt = time.time() - t0
                        print(f"📥 Gemini 返回完成（note_id={note_id},耗时={dt:.1f}s）")

                        raw_text = (getattr(resp, "text", None) or "").strip()
                        if not raw_text:
                            raise RuntimeError(f"Gemini 返回空文本：note_id={note_id}")

                        try:
                            result_dict = json.loads(raw_text)
                        except Exception:
                            start = raw_text.find("{")
                            end = raw_text.rfind("}")
                            if start != -1 and end != -1 and end > start:
                                result_dict = json.loads(raw_text[start : end + 1])
                            else:
                                raise

                        if not isinstance(result_dict, dict):
                            raise ValueError(f"解析结果不是 JSON 对象(dict)：{type(result_dict).__name__}")

                        # ✅ 标签合并逻辑：将 source_keyword 作为硬标签与 AI tags 合并
                        source_keyword = str(note.get("source_keyword", "") or "").strip()
                        hard_tag = source_keyword.lstrip("#").strip()
                        ai_tags_raw = str(result_dict.get("tags", "") or "").strip()
                        parts = []
                        if ai_tags_raw:
                            ai_tags_norm = (
                                ai_tags_raw.replace("，", ",")
                                .replace("\n", " ")
                                .replace("\t", " ")
                                .replace("  ", " ")
                            )
                            for chunk in ai_tags_norm.replace(",", " ").split():
                                t = chunk.strip().lstrip("#").strip()
                                if t:
                                    parts.append(t)
                        if hard_tag:
                            parts.append(hard_tag)

                        seen = set()
                        merged = []
                        for t in parts:
                            if t not in seen:
                                seen.add(t)
                                merged.append(f"#{t}")
                        result_dict["tags"] = " ".join(merged)

                        print(f"💾 写入数据库（note_id={note_id}）")
                        if not self.db.save_analysis(note_id, result_dict):
                            raise RuntimeError(f"保存分析结果失败：note_id={note_id}")
                        if not self.db.mark_as_analyzed(note_id):
                            raise RuntimeError(f"标记已分析失败：note_id={note_id}")

                        print(f"✅ 笔记 [{note_id}] 分析完成")
                        continue
                    except Exception as e2:
                        print(f"❌ 笔记 [{note_id}] 分析失败（回退重试也失败）：{e2}")
                        continue
                print(f"❌ 笔记 [{note_id}] 分析失败：{e}")

    def analyze_single_note(self, content: str, original_keyword: Optional[str] = None) -> Dict[str, str]:
        """单篇精细化分析：输出 Markdown 分析报告 + 智能标签。

        Returns:
            {"report": markdown_text, "tags": tag_list_string}
        """
        text = (content or "").strip()
        if not text:
            raise ValueError("content 为空，无法分析")

        ok = (original_keyword or "").strip()
        kw_tag = ""
        if ok:
            kw_tag = ok if ok.startswith("#") else f"#{ok}"

        system_prompt = (
            "你是一位小红书爆款拆解专家。请深度分析给定的这篇笔记。\n"
            "任务 1：深度拆解 输出一段 Markdown 格式的分析笔记，包含：\n"
            "🎭 用户画像：这篇笔记吸引谁？\n"
            "🧨 情绪钩子：第一句话是怎么抓住注意力的？\n"
            "🦴 结构拆解：行文逻辑是怎样的（如：痛点-误区-方案）？\n"
            "💡 可复用金句：提取 1-2 句最有杀伤力的原文。\n\n"
            "任务 2：智能打标签 基于笔记内容，提取 3-5 个分类标签（如：#职场干货, #Python入门, #情绪价值）。\n"
        )
        if kw_tag:
            system_prompt += f"注意：original_keyword 必须作为第一标签 ({kw_tag})，其余标签紧随其后。\n\n"

        system_prompt += (
            "输出要求：请只输出严格 JSON（不要输出 Markdown 代码块），结构如下：\n"
            "{\"report\": \"<Markdown分析内容>\", \"tags\": [\"#tag1\", \"#tag2\", \"#tag3\"]}\n"
        )

        prompt = f"{system_prompt}\n\n【笔记正文】\n{text}\n"

        print(f"📝 [analyze_single_note] 开始分析（关键词：{original_keyword or '无'}，内容长度：{len(text)} 字符）")
        
        last_err: Optional[Exception] = None
        attempt = 0
        max_retries = 3
        while attempt < max_retries:
            try:
                print(f"🌐 [analyze_single_note] 调用 Gemini API（尝试 {attempt+1}/{max_retries}，model={self.model_name}，timeout={self.timeout_seconds}s）")
                t0 = time.time()
                resp = self.model.generate_content(
                    prompt,
                    generation_config={
                        "temperature": self.temperature,
                        "max_output_tokens": self.max_output_tokens,
                    },
                    request_options={"timeout": self.timeout_seconds},
                )
                dt = time.time() - t0
                print(f"📥 [analyze_single_note] Gemini 返回完成（耗时={dt:.1f}s）")
                raw = (getattr(resp, "text", None) or "").strip()
                if not raw:
                    raise RuntimeError("Gemini 返回空文本")

                try:
                    data = json.loads(raw)
                except Exception:
                    l = raw.find("{")
                    r = raw.rfind("}")
                    if l >= 0 and r > l:
                        data = json.loads(raw[l : r + 1])
                    else:
                        raise

                report = str(data.get("report") or "").strip()
                tags_val = data.get("tags")

                tags_list: List[str] = []
                if isinstance(tags_val, list):
                    tags_list = [str(t).strip() for t in tags_val if str(t).strip()]
                elif isinstance(tags_val, str):
                    tags_list = [t.strip() for t in tags_val.replace("，", ",").split(",") if t.strip()]

                # 强制规则：搜索词永远第一标签
                final_tags: List[str] = []
                if kw_tag:
                    final_tags.append(kw_tag)
                for t in tags_list:
                    tt = t if t.startswith("#") else f"#{t}"
                    if tt not in final_tags:
                        final_tags.append(tt)

                print(f"✅ [analyze_single_note] 分析完成（报告长度：{len(report)} 字符，标签数：{len(final_tags)}）")
                return {"report": report, "tags": ",".join(final_tags)}

            except Exception as e:
                last_err = e
                error_msg = str(e)
                error_display = error_msg[:200] + "..." if len(error_msg) > 200 else error_msg
                print(f"⚠️  [analyze_single_note] 分析失败（尝试 {attempt+1}/{max_retries}）：{error_display}")
                
                # 404 模型回退不消耗重试次数
                if self._maybe_reinit_model_for_404(e):
                    print(f"🔄 [analyze_single_note] 模型已切换，继续重试...")
                    time.sleep(1)
                    continue
                attempt += 1
                if attempt < max_retries:
                    print(f"⏳ [analyze_single_note] 等待 1 秒后重试...")
                    time.sleep(1)

        print(f"❌ [analyze_single_note] 达到最大重试次数，分析失败")
        raise RuntimeError(f"单篇分析失败：{last_err}")

    def analyze_comments(self, comments_text: str) -> str:
        """
        分析小红书评论区内容，生成 VOC（Voice of Customer）洞察报告。
        
        Args:
            comments_text: 精选评论原文（可以是多条评论的拼接文本）
        
        Returns:
            str: Markdown 格式的分析报告，包含：
                - 😠 痛点反直觉：用户在抱怨什么？
                - ❓ 高频提问：大家都在问什么？
                - 😂 情绪共鸣：哪些梗引发了互动？
                - 💡 选题推荐：基于评论推荐 2 个选题方向。
        """
        text = (comments_text or "").strip()
        if not text:
            raise ValueError("comments_text 为空，无法分析")

        system_prompt = (
            "你是一位消费者心理学专家。请分析以下小红书评论区内容。\n"
            "输出 Markdown 报告，包含：\n\n"
            "😠 痛点反直觉：用户在抱怨什么？\n\n"
            "❓ 高频提问：大家都在问什么？\n\n"
            "😂 情绪共鸣：哪些梗引发了互动？\n\n"
            "💡 选题推荐：基于评论推荐 2 个选题方向。"
        )

        prompt = f"{system_prompt}\n\n【评论区内容】\n{text}\n"

        print(f"📝 [analyze_comments] 开始分析评论（内容长度：{len(text)} 字符）")
        
        last_err: Optional[Exception] = None
        attempt = 0
        max_retries = 3
        while attempt < max_retries:
            try:
                print(f"🌐 [analyze_comments] 调用 Gemini API（尝试 {attempt+1}/{max_retries}，model={self.model_name}，timeout={self.timeout_seconds}s）")
                t0 = time.time()
                resp = self.model.generate_content(
                    prompt,
                    generation_config={
                        "temperature": self.temperature,
                        "max_output_tokens": self.max_output_tokens,
                    },
                    request_options={"timeout": self.timeout_seconds},
                )
                dt = time.time() - t0
                print(f"📥 [analyze_comments] Gemini 返回完成（耗时={dt:.1f}s）")
                
                result = (getattr(resp, "text", None) or "").strip()
                if not result:
                    raise RuntimeError("Gemini 返回空文本")

                print(f"✅ [analyze_comments] 分析完成（报告长度：{len(result)} 字符）")
                return result

            except Exception as e:
                last_err = e
                error_msg = str(e)
                error_display = error_msg[:200] + "..." if len(error_msg) > 200 else error_msg
                print(f"⚠️  [analyze_comments] 分析失败（尝试 {attempt+1}/{max_retries}）：{error_display}")
                
                # 404 模型回退不消耗重试次数
                if self._maybe_reinit_model_for_404(e):
                    print(f"🔄 [analyze_comments] 模型已切换，继续重试...")
                    time.sleep(1)
                    continue
                attempt += 1
                if attempt < max_retries:
                    print(f"⏳ [analyze_comments] 等待 1 秒后重试...")
                    time.sleep(1)

        print(f"❌ [analyze_comments] 达到最大重试次数，分析失败")
        raise RuntimeError(f"评论分析失败：{last_err}")

    def _build_prompt(self, title: str, content: str) -> str:
        # Prompt = 配置里的 system_prompt + 笔记内容
        # 注意：system_prompt 已要求“仅输出纯 JSON”
        chunks = []
        if self.system_prompt:
            chunks.append(self.system_prompt)
        chunks.append(f"【标题】\n{title}\n\n【正文】\n{content}")
        return "\n\n".join(chunks).strip()

    def _extract_image_paths(self, note: Dict[str, Any]) -> List[str]:
        """
        兼容多种字段名：
        - image_paths: list[str] 或 逗号分隔字符串
        - images / image_list: list[str]
        - image_path: 单个路径
        """
        for key in ("image_paths", "images", "image_list"):
            v = note.get(key)
            if not v:
                continue
            if isinstance(v, list):
                return [str(x) for x in v if str(x).strip()]
            if isinstance(v, str):
                return [s.strip() for s in v.split(",") if s.strip()]

        v = note.get("image_path")
        if isinstance(v, str) and v.strip():
            return [v.strip()]
        return []

    def _load_images(self, image_paths: List[str]) -> List[Any]:
        """
        将本地图片路径转换为 Gemini 可接受的格式（PIL.Image）
        """
        images: List[Any] = []
        for p in image_paths:
            try:
                img_path = Path(p)
                if not img_path.is_absolute():
                    # 允许相对路径：相对项目根目录
                    project_root = Path(__file__).resolve().parents[2]
                    img_path = (project_root / img_path).resolve()
                if not img_path.exists():
                    print(f"⚠️ 图片不存在，跳过：{img_path}")
                    continue
                images.append(Image.open(img_path))
            except Exception as e:
                print(f"⚠️ 图片加载失败，跳过：{p}，原因：{e}")
        return images

    def generate_strategy_report(
        self,
        materials: List[Dict[str, Any]],
        *,
        topic_hint: str = "",
        request_timeout_seconds: Optional[int] = None,
    ) -> str:
        """
        基于外部素材（非数据库笔记）生成“策略报告”，用于 Streamlit 流水线中的 Analyst 阶段。

        Args:
            materials: 由 Scout/Deep Dive 得到的素材列表。建议字段：
                - title: str
                - url: str
                - content: str（全文/摘要均可）
            topic_hint: 主题/产品/赛道提示（可选）
            request_timeout_seconds: 覆盖超时（默认使用初始化时的 self.timeout_seconds）

        Returns:
            str: 策略报告（Markdown 友好）
        """
        if not materials:
            raise ValueError("materials 为空，无法生成策略报告。")

        timeout = int(request_timeout_seconds or self.timeout_seconds)

        # 尽量避免超长输入：每条素材截断到合理长度
        normalized: List[Dict[str, str]] = []
        for m in materials:
            title = str(m.get("title", "") or "").strip()
            url = str(m.get("url", "") or "").strip()
            content = str(m.get("content", "") or "").strip()
            if len(content) > 6000:
                content = content[:6000] + "\n...（内容过长已截断）"
            normalized.append({"title": title, "url": url, "content": content})

        system = (self.system_prompt or "").strip()
        hint = topic_hint.strip()

        prompt_parts = []
        if system:
            prompt_parts.append(system)
        prompt_parts.extend(
            [
                "【任务】你是一位小红书内容策略分析师。",
                "请基于下面的参考素材，输出一份可直接交给写作同学执行的“策略报告”。",
                "【输出要求】",
                "- 用中文输出（可用 Markdown）",
                "- 必须包含：目标用户画像、核心痛点/利益点、内容角度与差异化、标题公式(>=5)、结构大纲、素材引用要点(带来源序号)、风险与合规提醒、可复用话术/金句(>=10)",
                "- 如果素材不足以支撑某结论，请明确标注“推测/待验证”",
            ]
        )
        if hint:
            prompt_parts.append(f"【主题提示】{hint}")

        # 拼接素材
        prompt_parts.append("【参考素材】")
        for idx, m in enumerate(normalized, start=1):
            prompt_parts.append(
                "\n".join(
                    [
                        f"--- 素材 {idx} ---",
                        f"标题：{m['title'] or '（无）'}",
                        f"链接：{m['url'] or '（无）'}",
                        "正文：",
                        m["content"] or "（无正文）",
                    ]
                )
            )

        prompt = "\n\n".join(prompt_parts).strip()

        print(f"🧠 生成策略报告中...（model={self.model_name}, timeout={timeout}s, materials={len(materials)}）")
        t0 = time.time()
        resp = self.model.generate_content(
            prompt,
            generation_config={
                "temperature": max(0.2, min(self.temperature, 0.8)),
                "max_output_tokens": max(2048, self.max_output_tokens),
            },
            request_options={"timeout": timeout},
        )
        dt = time.time() - t0
        print(f"📥 策略报告返回完成（耗时={dt:.1f}s）")

        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            raise RuntimeError("生成策略报告失败：Gemini 返回空文本")
        return text
