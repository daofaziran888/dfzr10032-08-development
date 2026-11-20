# 多源数据采集与RPA自动化平台

面向网页抓取、API接入、增量采集、RPA流程编排、数据校验与采集监控的数据工程平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 数据源配置 | `datasource` | 数据源 | 源端编号、连接类型、接入地址 |
| 网页采集规则 | `crawler` | 采集规则 | 规则编号、目标网址、页面选择器 |
| API接入管理 | `apiclient` | API客户端 | 客户端编号、接口名称、请求方法 |
| 增量采集 | `incremental` | 增量游标 | 游标编号、来源标识、增量字段 |
| RPA任务编排 | `robot_task` | RPA任务 | 任务编号、任务名称、页面定位器 |
| 数据接入通道 | `ingestion` | 接入通道 | 通道编号、通道名称、输入格式 |
| 格式解析器 | `parser` | 解析配置 | 解析编号、输入格式、输出格式 |
| 采集结果校验 | `validator` | 校验规则 | 规则编号、校验字段、校验类型 |
| 采集去重 | `dedup` | 去重策略 | 策略编号、输入来源、去重键 |
| 采集调度 | `schedule` | 调度计划 | 计划编号、关联任务、调度周期 |
| 采集监控 | `monitor` | 监控面板 | 面板编号、采集源总数、今日采集量 |
| 采集日志 | `log_viewer` | 日志条目 | 日志编号、关联任务、日志级别 |
| 凭证管理 | `auth` | 认证凭证 | 凭证编号、凭证类型、关联数据源 |
| 采集转换管道 | `transform` | 转换节点 | 节点编号、输入来源、输出目标 |
| 数据流转编排 | `flow` | 流转管道 | 管道编号、管道名称、起始节点 |
| 数据血缘追踪 | `lineage` | 血缘关系 | 关系编号、上游节点、下游节点 |
| 采集报告 | `report` | 采集报告 | 报告编号、采集源、统计周期 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 上传文件接入与按格式解析

接入通道在「数据接入通道」页登记输入格式后，可通过同页的「上传文件接入」面板
把文件交给格式入口，形成**接入原始事件数据集**（`app/services/ingestion_import.py`）。

### 处理口径

1. **逐行留痕**：文件每个物理行落一条接入原始事件，带 `文件标识 + 原始行号 + 原文`。
   支持的格式登记在 `app/services/format_registry.py`（CSV-货运记录、CSV-通用文本、
   JSONL-事件行）；通道声明的格式未登记时导入会被拒绝。
2. **坏记录隔离**：引号未闭合、字段数不符、JSON 语法错误等坏行保留位置和原文，
   状态置为「坏记录」，可在「坏记录追查」里直接指回文件与行号，永远不会进入有效事件。
3. **两种交付策略**：
   - 可用先行：结构正常的行立即确认为获准事件，坏行挂起等修正；
   - 完整交付：只要存在坏行，整份文件阻断（好行保持「待投递」），修正后一次性释放。
4. **重投修正不重复交付**：通道维度记住已确认行号。重投文件可声明首行对应的原始行号，
   已确认行再次出现记为「已跳过」；修正成功的行沿用原行号确认，原坏记录保留原文、
   置为「已修正」并指向修正文件。
5. **解析明细可按原位置核对**：格式解析器（`POST /api/parser/{id}/run`）只读取
   「已确认」且未消费的事件，每条解析明细都回指 `文件标识 + 原始行号 + 原文`，
   已解析事件不会被二次交付。

### 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/ingestion/imports/formats` | 已登记可接入的输入格式 |
| POST | `/api/ingestion/imports/channels/{channel_id}/files` | 按通道声明格式导入上传文件 |
| GET | `/api/ingestion/imports/files` | 接入文件清单（含坏记录数、交付状态） |
| GET | `/api/ingestion/imports/files/{id}/events` | 文件的接入原始事件（按行号） |
| GET | `/api/ingestion/imports/files/{id}/dead-letters` | 坏记录追查清单 |
| POST | `/api/ingestion/imports/files/{id}/reimport` | 重投修正文件（已确认行自动跳过） |
| POST | `/api/parser/{id}/run` | 解析器读取获准事件并产出带行号的明细 |
| GET | `/api/ingestion/imports/parse-results` | 解析明细查询（关联文件与行号） |

后端测试：`cd backend && python3 tests/test_ingestion_flow.py`（含三行货运样本、
中间行缺闭合引号、坏记录指回第二行、重投后首末行不重复进入解析的完整核对）。
