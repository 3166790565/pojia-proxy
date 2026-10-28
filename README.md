# 破甲路由中转站

轻量级 LLM 中转路由：在转发请求前，把破甲提示词注入到请求的 system 提示词前面。
Python + FastAPI 实现，Docker 部署。

## 特性

- **双协议**：OpenAI `/v1/chat/completions` + Anthropic `/v1/messages`，共用同一注入引擎。
- **两种路由模式，可切换**：
  - **精细路由**：按 `model` 分类（claude / gpt / deepseek / gemini / glm / grok / other），
    再按消息关键词归入类别（通用 / 逆向 / 创作 / 渗透），组合出对应破甲词。
  - **破甲词包**：破甲词独立成命名包（冷咖啡 / 小码酱 / DevSec…），按模型 pattern 绑定，
    同一模型可叠加多个包（如 deepseek 同时用「小码酱 + 冷咖啡」）。
- **28 格全填充**：7 模型 × 4 类别，每格独立可编辑。缺现成素材时按已有破甲词架构自动派生。
- **注入位置可调**：前置 `prepend` / 后置 `append` / 替换 `replace`，全局默认 + 单格覆盖。
- **防重复注入**：双 needle 判重（头 80 + 尾 96 字符），重试/重放不会叠加。
- **完整 WebUI**：单管理员登录；破甲词按 family 手风琴展示，点开才拉全文。
- **key 透传**：中转不校验、不存储业务 key；`/v1/models` 同样透传上游。
- **SSE 透传**：逐 chunk 原样转发，旁路只读统计 usage / 首 token / 终态。

## 快速开始（Docker）

```bash
# 可选：用 .env 文件指定管理员密码，不填则首次启动打印随机密码
echo "ADMIN_PASSWORD=你的密码" > .env
docker compose up -d
```

> 若目录名含中文，Compose 无法推断项目名，请显式指定：
> `docker compose -p pojia-proxy up -d`

直接使用已发布镜像（无需构建）：

```bash
docker run -d --name pojia-relay \
  -p 127.0.0.1:8000:8000 \
  -e ADMIN_PASSWORD=你的密码 \
  -v relay_data:/data \
  bxacc123/pojia-router:latest
```

首次启动若未设置 `ADMIN_PASSWORD`，会打印一个随机管理员密码：

```bash
docker compose logs relay | grep -A1 初始密码
```

浏览器打开 `http://127.0.0.1:8000/`，用该密码登录。
`SECRET_KEY` 留空时自动生成并持久化到 `/data/.secret_key`（重启后会话仍有效）。

## 配置

| 环境变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `SECRET_KEY` | 否 | 自动生成 | 会话签名密钥；留空则生成并持久化到 `/data/.secret_key` |
| `ADMIN_PASSWORD` | 否 | 自动生成 | 管理员登录密码（明文） |
| `ADMIN_PASSWORD_HASH` | 否 | — | 兼容旧配置：scrypt 哈希串，优先级低于 `ADMIN_PASSWORD` |
| `DATA_DIR` | 否 | `./data` | 数据目录 |
| `DB_PATH` | 否 | `$DATA_DIR/relay.db` | SQLite 路径 |
| `PRESET_DIR` | 否 | `$DATA_DIR/presets` | 预设输出目录 |
| `REQUEST_TIMEOUT` | 否 | `300` | 上游超时（秒） |
| `MAX_BODY_BYTES` | 否 | `4194304` | 请求体上限 |
| `LOG_RETENTION_DAYS` | 否 | `7` | 日志保留天数 |

## 客户端使用

把客户端 base URL 指向本服务，key 填上游真实 key（原样透传）：

```bash
# OpenAI 兼容
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-your-upstream-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"你好"}]}'

# Anthropic
curl http://127.0.0.1:8000/v1/messages \
  -H "x-api-key: sk-ant-your-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-5","max_tokens":1024,"messages":[{"role":"user","content":"你好"}]}'
```

上游地址在 WebUI「设置」里配置。

## WebUI

- **设置**：上游地址、注入开关、**路由模式（精细路由 / 破甲词包）**、注入位置、超时、日志保留、CORS。
- **破甲词包**：独立命名的破甲词（名称/描述/正文），可增删改。
- **模型绑定**：`pattern` → 破甲词包 + 优先级；同一模型绑多个包即叠加注入。
- **模型规则**：精细路由的 `family` + `pattern`（glob 或 `re:` 正则）+ 优先级。
- **关键词**：精细路由的 `category` + 关键词（空格分隔，任一命中）。
- **精细破甲词**：7×4 手风琴视图，点 family 展开；点编辑在右侧抽屉加载全文。
- **请求日志**：分页 + 详情（路由模式 / 命中包 / usage / 错误）。

## 开发

```bash
pip install -r requirements.txt
DATA_DIR=./data python -m uvicorn app.main:app --reload --port 8000
pytest -q
```

## 目录结构

```
app/
  main.py         应用装配与生命周期
  config.py       环境变量与常量
  db.py           SQLite schema 与 CRUD
  security.py     scrypt 密码 + 签名会话
  selector.py     模型分类 + 关键词 + 预设选择 / 包绑定选择
  engine.py       三协议注入 + 判重 + ChatML 净化
  proxy.py        URL/header/SSE/脱敏
  proxy_api.py    /v1/* 代理路由
  admin_api.py    /admin/* 管理接口
  seed_builder.py 种子导入 + 缺料自动派生 + 默认包与绑定
presets/seed/     种子破甲词（{family}__{category}.md）
web/              单页前端
tests/            pytest
```

## 边界说明

- 不做协议互转：OpenAI 端点只转发 `/v1/chat/completions`，Anthropic 只转发 `/v1/messages`。
- 破甲效果取决于上游模型与策略，本项目只负责注入与转发。
- SQLite 单写者，Docker 使用单 worker。

## 镜像

- `bxacc123/pojia-router:latest`
- `bxacc123/pojia-router:1.0.0`

本地修改源码后重新发布：

```bash
docker build -t bxacc123/pojia-router:latest -t bxacc123/pojia-router:1.0.0 .
docker push bxacc123/pojia-router:latest
docker push bxacc123/pojia-router:1.0.0
```
