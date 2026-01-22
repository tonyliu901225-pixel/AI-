"""
数据库管家模块
基于 Python 原生 sqlite3，不使用 ORM，保持轻量级
所有 Agent 共享的数据仓库
"""

import sqlite3
import os
import json
from pathlib import Path
from typing import List, Dict, Optional


class DBManager:
    """数据库管理器 - 负责所有数据存储和查询操作"""
    
    def __init__(self, db_path: str = "database/redbook_core.db"):
        """
        初始化数据库连接
        
        Args:
            db_path: 数据库文件路径，默认为 database/redbook_core.db
        """
        # 如果路径是相对路径，转换为基于项目根目录的绝对路径
        if not os.path.isabs(db_path):
            # 获取项目根目录（假设 core/ 在项目根目录下）
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            db_path = os.path.join(project_root, db_path)
        
        # 确保 database 文件夹存在
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        
        self.db_path = db_path
        # 开启 check_same_thread=False 以支持多线程
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # 返回字典格式的结果
        
        # 自动建表
        self._create_tables()
    
    def _create_tables(self):
        """自动创建核心数据表"""
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            
            # 1. scraped_notes: 存储爬虫抓取的笔记数据
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scraped_notes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    likes INTEGER DEFAULT 0,
                    comment_count INTEGER DEFAULT 0,
                    post_date TEXT,
                    source_keyword TEXT,
                    tag_list TEXT,
                    processed INTEGER DEFAULT 0
                )
            """)

            # ✅ 兼容旧数据库：如果表已存在但缺少新列，则自动补齐
            # 说明：
            # - SQLite 支持 ALTER TABLE ADD COLUMN（只能追加列，不能在中间插入）
            # - 这里用 PRAGMA table_info 做一次“列存在性检查”，避免重复 ADD COLUMN 报错
            cursor.execute("PRAGMA table_info(scraped_notes);")
            existing_cols = {row[1] for row in cursor.fetchall()}  # row[1] = column name

            if "comment_count" not in existing_cols:
                cursor.execute("ALTER TABLE scraped_notes ADD COLUMN comment_count INTEGER DEFAULT 0;")

            if "tag_list" not in existing_cols:
                cursor.execute("ALTER TABLE scraped_notes ADD COLUMN tag_list TEXT;")

            # ✅ Analyst 单篇精细化分析：新增字段（兼容旧库，自动补齐）
            if "analysis_result" not in existing_cols:
                cursor.execute("ALTER TABLE scraped_notes ADD COLUMN analysis_result TEXT;")
            if "ai_tags" not in existing_cols:
                cursor.execute("ALTER TABLE scraped_notes ADD COLUMN ai_tags TEXT;")
            if "search_keyword" not in existing_cols:
                cursor.execute("ALTER TABLE scraped_notes ADD COLUMN search_keyword TEXT;")
            
            # ✅ VOC 评论分析：新增字段（兼容旧库，自动补齐）
            if "raw_comments" not in existing_cols:
                cursor.execute("ALTER TABLE scraped_notes ADD COLUMN raw_comments TEXT;")
            if "comments_insight" not in existing_cols:
                cursor.execute("ALTER TABLE scraped_notes ADD COLUMN comments_insight TEXT;")
            
            # 2. analysis_results: 存储智库分析结果
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analysis_results (
                    note_id TEXT PRIMARY KEY,
                    visual_prompt TEXT,
                    title_formula TEXT,
                    structure_logic TEXT,
                    tags TEXT,
                    risk_points TEXT,
                    raw_json TEXT,
                    FOREIGN KEY (note_id) REFERENCES scraped_notes(id)
                )
            """)

            # ✅ 兼容旧数据库：analysis_results 自动补齐缺失列
            cursor.execute("PRAGMA table_info(analysis_results);")
            analysis_cols = {row[1] for row in cursor.fetchall()}
            if "structure_logic" not in analysis_cols:
                cursor.execute("ALTER TABLE analysis_results ADD COLUMN structure_logic TEXT;")
            if "raw_json" not in analysis_cols:
                cursor.execute("ALTER TABLE analysis_results ADD COLUMN raw_json TEXT;")
            
            # 3. note_stats: 存储笔记监控数据（点赞、收藏等）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS note_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id TEXT NOT NULL,
                    record_time TEXT NOT NULL,
                    likes INTEGER DEFAULT 0,
                    collects INTEGER DEFAULT 0,
                    FOREIGN KEY (note_id) REFERENCES scraped_notes(id)
                )
            """)
            
            # 4. generated_works: 存储生成的文案作品
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS generated_works (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    persona_name TEXT NOT NULL,
                    title TEXT,
                    content TEXT NOT NULL,
                    title_formula TEXT,
                    structure_logic TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
            """)
            
            # 5. feedback_logs: 存储人工反馈记录
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS feedback_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_id INTEGER,
                    persona_name TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                    feedback_text TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    FOREIGN KEY (work_id) REFERENCES generated_works(id)
                )
            """)
            
            conn.commit()
    
    def add_note(self, note_data: Dict) -> bool:
        """
        添加笔记到数据库
        
        使用 INSERT OR IGNORE 语句，如果 id 已存在则直接忽略
        这样可以节省后续 Agent 的 Token，避免重复处理
        
        Args:
            note_data: 笔记数据字典，必须包含以下字段：
                - id: 笔记唯一ID (主键)
                - title: 标题
                - content: 正文内容
                - likes: 点赞数
                - post_date: 发布日期
                - source_keyword: 来源关键词
                可选字段：
                - comment_count: 评论数（默认0）
                - tag_list: 标签列表
                - analysis_result: 分析结果
                - ai_tags: AI生成的标签
                - search_keyword: 搜索关键词
                - raw_comments: 精选评论原文（VOC分析）
                - comments_insight: AI分析后的洞察（VOC分析）
        
        Returns:
            bool: 是否成功插入（如果已存在则返回 False）
        """
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO scraped_notes 
                    (id, title, content, likes, comment_count, post_date, source_keyword, tag_list, processed, analysis_result, ai_tags, search_keyword, raw_comments, comments_insight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """, (
                    note_data.get('id'),
                    note_data.get('title', ''),
                    note_data.get('content', ''),
                    note_data.get('likes', 0),
                    note_data.get('comment_count', 0),
                    note_data.get('post_date', ''),
                    note_data.get('source_keyword', ''),
                    note_data.get('tag_list', ''),
                    note_data.get('analysis_result', ''),
                    note_data.get('ai_tags', ''),
                    # 兼容：如未显式提供 search_keyword，则回退到 source_keyword
                    (note_data.get('search_keyword') or note_data.get('source_keyword') or ''),
                    note_data.get('raw_comments', ''),
                    note_data.get('comments_insight', ''),
                ))
                
                conn.commit()
                # 如果插入成功，rowcount 会大于 0
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                print(f"插入笔记时出错: {e}")
                return False
    
    def get_pending_notes(self, limit: int = 5) -> List[Dict]:
        """
        获取待处理的笔记（processed=0）
        
        Args:
            limit: 返回的最大记录数，默认 5
        
        Returns:
            List[Dict]: 待处理笔记列表，每个元素是一个字典
        """
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row  # 设置 row_factory
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, title, content, likes, comment_count, post_date, source_keyword, tag_list, processed, analysis_result, ai_tags, search_keyword, raw_comments, comments_insight
                FROM scraped_notes
                WHERE processed = 0
                ORDER BY post_date DESC
                LIMIT ?
            """, (limit,))
            
            rows = cursor.fetchall()
            # 将 Row 对象转换为字典
            return [{key: row[key] for key in row.keys()} for row in rows]

    def get_notes_by_keyword(
        self,
        keyword: str,
        limit: int = 50,
        *,
        order_by: str = "likes",
        descending: bool = True,
    ) -> List[Dict]:
        """
        按 source_keyword 查询已抓取的小红书笔记，用于前端 UI 同步展示。

        Args:
            keyword: 关键词（scraped_notes.source_keyword）
            limit: 返回条数
            order_by: 排序字段：likes / post_date / comment_count
            descending: 是否倒序

        Returns:
            List[Dict]: 笔记列表
        """
        kw = (keyword or "").strip()
        if not kw:
            return []

        allowed = {"likes", "post_date", "comment_count"}
        ob = order_by if order_by in allowed else "likes"
        direction = "DESC" if descending else "ASC"

        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, title, content, likes, comment_count, post_date, source_keyword, tag_list, processed, analysis_result, ai_tags, search_keyword, raw_comments, comments_insight
                FROM scraped_notes
                WHERE source_keyword = ?
                ORDER BY {ob} {direction}
                LIMIT ?
                """,
                (kw, int(limit)),
            )
            rows = cursor.fetchall()
            return [{key: row[key] for key in row.keys()} for row in rows]

    def count_notes_by_keyword(self, keyword: str) -> int:
        """统计某个关键词已入库笔记数量（scraped_notes.source_keyword）。"""
        kw = (keyword or "").strip()
        if not kw:
            return 0
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(1) FROM scraped_notes WHERE source_keyword = ?",
                (kw,),
            )
            row = cursor.fetchone()
            return int(row[0] or 0) if row else 0

    def get_latest_note_by_keyword(self, keyword: str) -> Optional[Dict]:
        """获取某关键词最新入库的一条笔记（按 rowid 倒序）。"""
        kw = (keyword or "").strip()
        if not kw:
            return None
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, content, likes, comment_count, post_date, source_keyword, tag_list, processed, analysis_result, ai_tags, search_keyword, raw_comments, comments_insight
                FROM scraped_notes
                WHERE source_keyword = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (kw,),
            )
            row = cursor.fetchone()
            return {k: row[k] for k in row.keys()} if row else None
    
    def mark_as_analyzed(self, note_id: str) -> bool:
        """
        标记笔记为已分析状态（processed=1）
        
        Args:
            note_id: 笔记的唯一ID
        
        Returns:
            bool: 是否成功更新
        """
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    UPDATE scraped_notes
                    SET processed = 1
                    WHERE id = ?
                """, (note_id,))
                
                conn.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                print(f"更新笔记状态时出错: {e}")
                return False

    def save_analysis(self, note_id: str, result_dict: Dict) -> bool:
        """
        保存 Analyst 的结构化分析结果到 analysis_results

        Args:
            note_id: 笔记唯一 ID
            result_dict: Gemini 返回的 JSON（dict）

        Returns:
            bool: 是否成功写入
        """
        visual_prompt = result_dict.get("visual_prompt", "")
        title_formula = result_dict.get("title_formula", "")
        structure_logic = result_dict.get("structure_logic", "")
        tags = result_dict.get("tags", "")
        risk_points = result_dict.get("risk_points", "")
        raw_json = json.dumps(result_dict, ensure_ascii=False)

        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            try:
                # 使用 INSERT OR REPLACE，保证幂等（同 note_id 重新分析可覆盖）
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO analysis_results
                    (note_id, visual_prompt, title_formula, structure_logic, tags, risk_points, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (note_id, visual_prompt, title_formula, structure_logic, tags, risk_points, raw_json),
                )
                conn.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                print(f"保存分析结果时出错: {e}")
                return False

    def get_notes_pending_analysis(
        self, 
        limit: int = 100, 
        search_keyword: Optional[str] = None
    ) -> List[Dict]:
        """
        获取"待分析"的笔记：
        - analysis_result 为空 或 processed=0
        - 如果提供了 search_keyword，只返回匹配的笔记
        
        Args:
            limit: 返回的最大记录数
            search_keyword: 可选的搜索关键词筛选（如果提供，只返回匹配的笔记）
        
        Returns:
            List[Dict]: 待分析笔记列表
        """
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if search_keyword and search_keyword.strip():
                # 支持按搜索关键词筛选
                kw = search_keyword.strip()
                cursor.execute(
                    """
                    SELECT id, title, content, likes, comment_count, post_date, source_keyword, tag_list, processed, analysis_result, ai_tags, search_keyword, raw_comments, comments_insight
                    FROM scraped_notes
                    WHERE (analysis_result IS NULL OR TRIM(analysis_result) = '' OR processed = 0)
                      AND (COALESCE(NULLIF(TRIM(search_keyword), ''), source_keyword) = ?)
                    ORDER BY post_date DESC, rowid DESC
                    LIMIT ?
                    """,
                    (kw, int(limit)),
                )
            else:
                # 不筛选关键词
                cursor.execute(
                    """
                    SELECT id, title, content, likes, comment_count, post_date, source_keyword, tag_list, processed, analysis_result, ai_tags, search_keyword, raw_comments, comments_insight
                    FROM scraped_notes
                    WHERE (analysis_result IS NULL OR TRIM(analysis_result) = '' OR processed = 0)
                    ORDER BY post_date DESC, rowid DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                )
            
            rows = cursor.fetchall()
            return [{key: row[key] for key in row.keys()} for row in rows]

    def get_notes_by_ids(self, note_ids: List[str]) -> List[Dict]:
        """按 id 批量获取笔记（用于前端批量分析）。"""
        ids = [str(x).strip() for x in (note_ids or []) if str(x).strip()]
        if not ids:
            return []
        placeholders = ",".join(["?"] * len(ids))
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, title, content, likes, comment_count, post_date, source_keyword, tag_list, processed, analysis_result, ai_tags, search_keyword, raw_comments, comments_insight
                FROM scraped_notes
                WHERE id IN ({placeholders})
                """,
                ids,
            )
            rows = cursor.fetchall()
            return [{key: row[key] for key in row.keys()} for row in rows]

    @staticmethod
    def _split_tags(tag_str: str) -> List[str]:
        if not tag_str:
            return []
        parts = []
        for p in str(tag_str).replace("，", ",").split(","):
            t = p.strip()
            if not t:
                continue
            # 统一为 #tag 形式
            if not t.startswith("#"):
                t = f"#{t}"
            parts.append(t)
        # 去重保序
        seen = set()
        out = []
        for t in parts:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    @staticmethod
    def _join_tags(tags: List[str]) -> str:
        # DB 内仍用逗号分隔字符串
        return ",".join([t.strip() for t in tags if t and str(t).strip()])

    def update_note_analysis(self, note_id: str, analysis_text: str, ai_tags: str) -> bool:
        """
        回写单篇分析结果，并更新标签体系。

        标签规则（强制执行）：
        Final Tags = [Original Search Keyword] + [Manual Tags] + [AI Generated Tags]
        且“搜索词”永远是第一标签。
        """
        nid = str(note_id or "").strip()
        if not nid:
            return False

        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 读取现有 search_keyword / tag_list
            cursor.execute(
                """
                SELECT search_keyword, source_keyword, tag_list
                FROM scraped_notes
                WHERE id = ?
                LIMIT 1
                """,
                (nid,),
            )
            row = cursor.fetchone()
            if not row:
                return False

            original_kw = (row["search_keyword"] or row["source_keyword"] or "").strip()
            manual_tags = self._split_tags(row["tag_list"] or "")
            ai_tags_list = self._split_tags(ai_tags or "")

            final_tags: List[str] = []
            if original_kw:
                kw_tag = original_kw if original_kw.startswith("#") else f"#{original_kw}"
                final_tags.append(kw_tag)
            # manual tags（去掉可能与 kw 重复）
            for t in manual_tags:
                if t not in final_tags:
                    final_tags.append(t)
            # ai tags
            for t in ai_tags_list:
                if t not in final_tags:
                    final_tags.append(t)

            final_tag_str = self._join_tags(final_tags)
            ai_tag_str = self._join_tags(ai_tags_list)

            try:
                cursor.execute(
                    """
                    UPDATE scraped_notes
                    SET analysis_result = ?,
                        ai_tags = ?,
                        tag_list = ?,
                        processed = 1,
                        search_keyword = COALESCE(NULLIF(TRIM(search_keyword), ''), ?)
                    WHERE id = ?
                    """,
                    (
                        str(analysis_text or ""),
                        ai_tag_str,
                        final_tag_str,
                        original_kw,
                        nid,
                    ),
                )
                conn.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                print(f"保存单篇分析回写失败: {e}")
                return False
    
    def update_note(self, note_id: str, note_data: Dict) -> bool:
        """
        更新笔记数据（通用更新方法）
        
        支持更新笔记的任意字段，包括新添加的 raw_comments 和 comments_insight。
        只更新 note_data 中提供的字段，未提供的字段保持不变。
        
        Args:
            note_id: 笔记唯一ID
            note_data: 要更新的字段字典，可包含以下字段：
                - title: 标题
                - content: 正文内容
                - likes: 点赞数
                - comment_count: 评论数
                - post_date: 发布日期
                - source_keyword: 来源关键词
                - search_keyword: 搜索关键词
                - tag_list: 标签列表
                - analysis_result: 分析结果
                - ai_tags: AI生成的标签
                - raw_comments: 精选评论原文
                - comments_insight: AI分析后的洞察
                - processed: 处理状态
        
        Returns:
            bool: 是否成功更新
        """
        nid = str(note_id or "").strip()
        if not nid:
            return False
        
        # 构建动态更新语句
        update_fields = []
        update_values = []
        
        # 定义可更新的字段列表
        allowed_fields = [
            'title', 'content', 'likes', 'comment_count', 'post_date',
            'source_keyword', 'search_keyword', 'tag_list', 'analysis_result',
            'ai_tags', 'raw_comments', 'comments_insight', 'processed'
        ]
        
        for field in allowed_fields:
            if field in note_data:
                update_fields.append(f"{field} = ?")
                update_values.append(note_data[field])
        
        if not update_fields:
            # 没有要更新的字段
            return False
        
        update_values.append(nid)  # WHERE 条件的值
        
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            try:
                query = f"""
                    UPDATE scraped_notes
                    SET {', '.join(update_fields)}
                    WHERE id = ?
                """
                cursor.execute(query, update_values)
                conn.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                print(f"更新笔记时出错: {e}")
                return False
    
    def save_generated_work(
        self,
        persona_name: str,
        title: str,
        content: str,
        title_formula: Optional[str] = None,
        structure_logic: Optional[str] = None,
    ) -> Optional[int]:
        """
        保存生成的文案作品到 generated_works 表
        
        Args:
            persona_name: 人设名称
            title: 文案标题
            content: 文案正文
            title_formula: 标题公式（可选）
            structure_logic: 结构逻辑（可选）
        
        Returns:
            Optional[int]: 插入的工作 ID，失败返回 None
        """
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT INTO generated_works 
                    (persona_name, title, content, title_formula, structure_logic, created_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
                """, (
                    persona_name,
                    title,
                    content,
                    title_formula,
                    structure_logic,
                ))
                
                conn.commit()
                return cursor.lastrowid
            except sqlite3.Error as e:
                print(f"❌ 保存生成作品时出错: {e}")
                return None
    
    def log_feedback(
        self,
        work_id: Optional[int],
        persona_name: str,
        rating: int,
        feedback_text: Optional[str] = None,
    ) -> bool:
        """
        记录用户反馈
        
        Args:
            work_id: 关联的 generated_works 表的 ID（可为 None）
            persona_name: 人设名称
            rating: 用户打分 1-5
            feedback_text: 用户的具体修改意见（可选）
        
        Returns:
            bool: 是否成功插入
        """
        if not (1 <= rating <= 5):
            print(f"❌ 评分必须在 1-5 之间，当前值: {rating}")
            return False
        
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT INTO feedback_logs 
                    (work_id, persona_name, rating, feedback_text, created_at)
                    VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
                """, (
                    work_id,
                    persona_name,
                    rating,
                    feedback_text,
                ))
                
                conn.commit()
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                print(f"❌ 插入反馈记录时出错: {e}")
                return False
    
    def search_notes(
        self,
        keyword: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """
        搜索笔记：支持关键词模糊搜索和标签筛选。
        
        Args:
            keyword: 关键词（在 title 或 content 中模糊匹配）
            tags: 标签列表（tag_list 字段需包含任一标签）
            limit: 返回最大条数
        
        Returns:
            List[Dict]: 笔记列表，按 post_date 倒序排列
        """
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            conditions = []
            params = []
            
            # 关键词搜索（title 或 content 模糊匹配）
            if keyword and keyword.strip():
                kw = f"%{keyword.strip()}%"
                conditions.append("(title LIKE ? OR content LIKE ?)")
                params.extend([kw, kw])
            
            # 标签筛选（tag_list 包含任一标签）
            if tags and isinstance(tags, list) and len(tags) > 0:
                tag_conditions = []
                for tag in tags:
                    if tag and tag.strip():
                        tag_conditions.append("tag_list LIKE ?")
                        params.append(f"%{tag.strip()}%")
                if tag_conditions:
                    conditions.append(f"({' OR '.join(tag_conditions)})")
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            
            query = f"""
                SELECT id, title, content, likes, comment_count, post_date, source_keyword, tag_list, processed, analysis_result, ai_tags, search_keyword, raw_comments, comments_insight
                FROM scraped_notes
                WHERE {where_clause}
                ORDER BY post_date DESC, rowid DESC
                LIMIT ?
            """
            params.append(int(limit))
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [{key: row[key] for key in row.keys()} for row in rows]
    
    def add_note_manual(
        self,
        title: str,
        content: str,
        source: str = "Manual",
        tags: Optional[List[str]] = None,
        search_keyword: Optional[str] = None,
        raw_comments: Optional[str] = None,
    ) -> bool:
        """
        手动添加一条笔记到数据库。
        
        Args:
            title: 笔记标题
            content: 笔记正文
            source: 来源标识（默认 "Manual"）
            tags: 标签列表（会转为逗号分隔字符串）
            search_keyword: 搜索关键词（可选）
            raw_comments: 精选评论原文（可选，用于 VOC 分析）
        
        Returns:
            bool: 是否成功插入
        """
        import uuid
        from datetime import datetime
        
        note_id = f"manual_{uuid.uuid4().hex[:16]}"
        tag_str = ",".join(tags) if tags and isinstance(tags, list) else (tags if isinstance(tags, str) else "")
        post_date = datetime.now().strftime("%Y-%m-%d")
        
        note_data = {
            "id": note_id,
            "title": str(title).strip(),
            "content": str(content).strip(),
            "likes": 0,
            "comment_count": 0,
            "post_date": post_date,
            "source_keyword": str(source).strip(),
            "search_keyword": str(search_keyword or "").strip() or str(source).strip(),
            "tag_list": tag_str,
            "raw_comments": str(raw_comments or "").strip(),
        }
        
        return self.add_note(note_data)
    
    def get_all_search_keywords(self) -> List[str]:
        """
        获取数据库中所有唯一的搜索关键词列表（用于筛选）
        
        Returns:
            List[str]: 去重后的搜索关键词列表，按字母顺序排序
        """
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT COALESCE(NULLIF(TRIM(search_keyword), ''), source_keyword) as kw
                FROM scraped_notes
                WHERE COALESCE(NULLIF(TRIM(search_keyword), ''), source_keyword) IS NOT NULL
                  AND COALESCE(NULLIF(TRIM(search_keyword), ''), source_keyword) != ''
                ORDER BY kw
            """)
            rows = cursor.fetchall()
            keywords = [str(row[0]).strip() for row in rows if row[0] and str(row[0]).strip()]
            return sorted(list(set(keywords)))

    def get_all_tags(self) -> List[str]:
        """
        获取数据库中所有已存在的标签列表（从 tag_list 字段提取）。
        
        Returns:
            List[str]: 去重后的标签列表，按字母顺序排序
        """
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT tag_list FROM scraped_notes WHERE tag_list IS NOT NULL AND tag_list != ''")
            rows = cursor.fetchall()
            
            all_tags = set()
            for row in rows:
                tag_str = str(row[0] or "").strip()
                if tag_str:
                    # tag_list 可能是逗号分隔的字符串，需要拆分
                    tags = [t.strip() for t in tag_str.split(",") if t.strip()]
                    all_tags.update(tags)
            
            return sorted(list(all_tags))
    
    def get_recent_feedback(self, persona_name: str, limit: int = 3) -> List[str]:
        """
        获取该人设最近的负面反馈或带有文字建议的反馈
        
        逻辑：只获取该人设最近的、评分低于 4 分的、或者带有具体文字建议的记录。按时间倒序排列。
        
        Args:
            persona_name: 人设名称
            limit: 返回的最大记录数，默认 3
        
        Returns:
            List[str]: 纯文本列表，例如 ["不要用感叹号", "语气太生硬"]
        """
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            try:
                # 获取评分低于 4 分或者带有文字建议的记录
                cursor.execute("""
                    SELECT DISTINCT feedback_text
                    FROM feedback_logs
                    WHERE persona_name = ?
                      AND feedback_text IS NOT NULL
                      AND feedback_text != ''
                      AND (rating < 4 OR feedback_text IS NOT NULL)
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (persona_name, limit))
                
                rows = cursor.fetchall()
                # 提取 feedback_text 并过滤空值，同时去重
                feedback_set = set()
                feedback_list = []
                for row in rows:
                    text = row["feedback_text"].strip() if row["feedback_text"] else ""
                    if text and text not in feedback_set:
                        feedback_set.add(text)
                        feedback_list.append(text)
                
                return feedback_list
            except sqlite3.Error as e:
                print(f"❌ 查询反馈记录时出错: {e}")
                return []
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
