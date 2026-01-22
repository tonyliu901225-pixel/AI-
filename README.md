# AI小红书矩阵系统（本仓库使用说明）

本项目是你在本机初始化的“小红书 AI 矩阵系统”工作目录，核心目标是：
- **按关键词抓取小红书笔记**（优先按点赞排序）
- **本地清洗过滤**（减少垃圾数据，节省后续 Agent Token）
- **落库到 SQLite**（`database/redbook_core.db`，全局共享“记忆仓库”）
- **评论单独输出为 CSV**，封面单独落盘到本地目录

> 说明：`skills/scout/scout_skill/` 目录来自 MediaCrawler 项目（原爬虫工程），我们在其基础上做了“配置/入库/清洗/封面下载”等适配。

---

## 目录结构（你最关心的几个）

- `config/scout_rules.yaml`：**抓取规则配置（规则大脑）**
- `core/config_loader.py`：加载规则（支持 `SCOUT_RULES_PATH`）
- `core/db_manager.py`：SQLite 数据库管家（记忆仓库）
- `database/redbook_core.db`：项目根目录的 SQLite 数据库文件（✅最终以它为准）
- `test_scout.py`：一键跑“关键词抓取”的测试入口（支持命令行传关键词）
- `skills/scout/scout_skill/data/xhs/csv/`：**评论 CSV 输出目录**
- `skills/scout/scout_skill/data/xhs/images/<note_id>/cover.jpg`：**封面图片落盘路径**

---

## 1. 环境准备（macOS）

### 1.1 Python
建议使用 `python3`（macOS 默认 `python` 可能不存在）：

```bash
python3 --version
```

### 1.2 安装依赖（Playwright）
如果你还没装过 Playwright 浏览器依赖：

```bash
python3 -m pip install -U pip
python3 -m pip install playwright PyYAML
python3 -m playwright install chromium
```

> 你项目里 `skills/scout/scout_skill/requirements.txt` 也可以按需安装（但不建议盲装全部，缺什么补什么即可）。

---

## 2. 配置抓取规则（config/scout_rules.yaml）

编辑：`config/scout_rules.yaml`

关键字段说明：
- **crawl_behavior.headless_mode**：是否无头（调试/滑块建议 false）
- **crawl_behavior.sort_by**：排序策略  
  - `likes_descending`：按点赞/热度排序（✅推荐）
  - `time_descending`：按时间
  - `comment_descending`：按评论数（本地排序）
- **crawl_behavior.max_notes_to_crawl**：单次最多入库笔记数
- **crawl_behavior.goto_timeout_ms**：页面打开超时（网络慢就调大）
- **data_filters**：清洗阈值（正文长度、点赞、时间）

---

## 3. 一键运行：按关键词抓取（推荐入口）

### 3.1 运行命令

```bash
cd "/Users/apple/Desktop/AI小红书矩阵系统"
python3 test_scout.py "宠物医生"
```

### 3.2 运行时会做什么
- 打开浏览器（CDP 模式优先，必要时自动降级）
- 关键词搜索 → **按点赞排序** → 抓取详情
- 通过 `_is_valid_note()` 本地清洗
- 将合格笔记写入 SQLite：`database/redbook_core.db`
- 抓取评论并写入 CSV（评论单独文件）
- 下载封面到本地（如开启媒体下载）

---

## 4. 数据保存位置（非常重要）

### 4.1 笔记数据（SQLite）
最终数据库：`database/redbook_core.db`

表：`scraped_notes`
字段（当前要求）：
- `id`（主键，去重依据）
- `title`
- `content`
- `likes`（int）
- `comment_count`（int）
- `tag_list`（TEXT，逗号拼接）
- `post_date`（TEXT，YYYY-MM-DD）
- `source_keyword`
- `processed`（0/1）

### 4.2 评论数据（单独 CSV 文件）
目录：
- `skills/scout/scout_skill/data/xhs/csv/`

文件名规则：
- `{crawler_type}_comments_{YYYY-MM-DD}.csv`
例如：
- `skills/scout/scout_skill/data/xhs/csv/search_comments_2026-01-20.csv`

### 4.3 封面图片（cover.jpg）
目录规则：
- `skills/scout/scout_skill/data/xhs/images/<note_id>/cover.jpg`

示例：
- `skills/scout/scout_skill/data/xhs/images/691c706a000000001e00f25b/cover.jpg`

> 是否下载封面受 `skills/scout/scout_skill/config/base_config.py` 的 `ENABLE_GET_MEIDAS` 控制。

---

## 5. 如何查看数据库（sqlite3）

```bash
cd "/Users/apple/Desktop/AI小红书矩阵系统"
sqlite3 database/redbook_core.db
```

进入后：

```sql
.tables
.schema scraped_notes
.headers on
.mode column
SELECT COUNT(*) AS total FROM scraped_notes;
SELECT id, title, likes, comment_count, tag_list, post_date, source_keyword, processed
FROM scraped_notes
ORDER BY rowid DESC
LIMIT 20;
.quit
```

---

## 6. 去重规则（你当前项目已固定）

### 6.1 笔记去重
- **去重字段**：`scraped_notes.id`（主键）
- **策略**：`INSERT OR IGNORE`  
  - 同一 `id` 再次抓到会被忽略（不会更新旧记录）

> 如果你想“已存在则更新 likes/comment_count/tag_list”，需要把插入改为 UPSERT（可随时加）。

### 6.2 评论去重（CSV/JSON）
- **去重字段**：`comment_id`
- **策略**：写文件前检查已存在则跳过

---

## 7. 测试专用规则（用于跑“可结束”的测试）

你可以用环境变量 `SCOUT_RULES_PATH` 指定测试规则文件，避免改动正式规则：

```bash
cd "/Users/apple/Desktop/AI小红书矩阵系统"
SCOUT_RULES_PATH="/Users/apple/Desktop/AI小红书矩阵系统/config/scout_rules_test_petdoctor.yaml" \
python3 test_scout.py "宠物医生"
```

---

## 8. 常见问题（高频）

### 8.1 Page.goto Timeout
通常是网络/梯子/平台响应慢：
- 调大 `crawl_behavior.goto_timeout_ms`
- 将 `headless_mode: false`（有头更稳，便于手动过滑块）

### 8.2 CDP 端口 9222 冲突/不可用
确保没有残留 Chrome 进程占用 9222；或在 `skills/scout/scout_skill/config/base_config.py` 修改 `CDP_DEBUG_PORT`。

---

## 9. 下一步建议
- 增加“已存在则更新(UPSERT)”策略（让 likes/comment_count 随时间刷新）
- 评论也落库到 `database/redbook_core.db`（与笔记同一个“记忆仓库”）
- 加一个 `test_reports/` 自动生成测试报告脚本（可复现跑数+汇总）

