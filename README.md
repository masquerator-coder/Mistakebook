# 错题本

家里自用的错题记录与分析工具。跑在自己电脑上，平板通过局域网访问，
无账号、无云端、数据只存在本机。

## 功能

| 功能 | 做了什么 | 在哪里 |
|---|---|---|
| 1. 分科记录错题 | 拍照或打字录入，按科目和试卷批次归类 | 「错题」+「＋ 录错题」 |
| 2. 找学习漏洞 | 知识点覆盖率盘点、薄弱点排序、复习正确率趋势 | 「学情」「覆盖」 |
| 3. 讲解错题 | 本地多模态模型直接读题图讲解，指出错在哪一步 | 「错题」详情页 → 🤖 |
| 4. 定期归纳 | 按已统计的数据生成 7 天复习计划与提分建议 | 「计划」 |

## 快速开始

```bash
# 1. 准备配置（.env 不进版本库）
cp .env.example .env
#    按需修改 DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY

# 2. 启动
docker compose up -d

# 3. 看是否起来了
docker compose ps
curl http://localhost:8000/health
```

浏览器打开 `http://localhost:8000`，后台管理在 `http://localhost:8000/admin`。

平板访问用宿主机的局域网 IP（不是 localhost）：

```powershell
# 查当前局域网地址
.\scripts\find-url.ps1
```

## 配置项

全部通过 `.env` 或环境变量注入，见 `.env.example`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_BASE_URL` | `http://fq-dgx-01:8888/v1` | OpenAI 兼容端点 |
| `DEEPSEEK_API_KEY` | `fq-dgx` | 本地端点用的 key |
| `DEEPSEEK_MODEL` | `deepseek-v4.1-flash` | 模型 id |
| `POSTGRES_*` | `mistakebook` | 数据库账号 |
| `API_PORT` | `8000` | 宿主机端口 |
| `DAILY_REVIEW_MINUTES` | `20` | 每日建议复习时长，喂给计划生成 |

## 模型注意事项（踩过的坑）

本机端点是 **sglang**，有两条与官方云 API 不同的行为，改代码前务必知道：

1. **图片块绝不能带 `detail` 字段。** 带 `"detail":"original"` / `"low"` / `"high"`
   一律 HTTP 400。而且报错文本是 pydantic 对 union 成员逐个失败的几十条
   validation error，**完全不会提到 `detail`**，极易误判成 `type` 写错。
   正确写法只有：
   ```json
   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
   ```
2. 图片放 `system` 消息里也能用（官方云 API 不允许），但代码统一放 `user`，
   以便日后切回云 API 不用改。

另外：`response_format={"type":"json_object"}` 可用；`GET /v1/models` 只有
`deepseek-v4.1-flash` 一个模型。

## 数据与备份

```
data/uploads/   题目原图（按年月日分目录）
data/books/     教材 PDF（本地私有，见下）
data/pgdata/    Postgres 数据目录
```

### 备份（推荐用 pg_dump，不要直接拷 pgdata）

```powershell
# 1) 导出数据库（文本格式，跨版本可恢复，能看清内容）
docker compose exec -T db pg_dump -U mistakebook -d mistakebook `
  | Out-File -Encoding utf8 "backup\mistakebook-$(Get-Date -Format yyyyMMdd).sql"

# 2) 拷走上传的题目图片
Copy-Item -Recurse data\uploads "backup\uploads-$(Get-Date -Format yyyyMMdd)"
```

> **不要**在 Postgres 运行时直接拷 `data/pgdata/` —— 可能拷到写了一半的文件，
> 恢复后库会损坏。要么用上面的 `pg_dump`，要么先 `docker compose stop db` 再拷目录。

### 恢复

```powershell
Get-Content "backup\mistakebook-20260927.sql" -Raw `
  | docker compose exec -T db psql -U mistakebook -d mistakebook
```

（Windows 上必须用 `Get-Content -Raw | ...` 管道，直接 `< file` 会让 PowerShell 报语法错误。）

**恢复一条被误删的错题**：删除是软删除（只写 `deleted_at`），
调用 `POST /api/mistakes/{id}/restore` 即可恢复，图片和复习历史都还在。

## 版权边界（重要）

- `data/books/` 里的教材 PDF 是**本地私有资源**：已在 `.gitignore` 中排除，
  **不要提交、不要分发、不要随应用打包**。
- 知识点骨架与教材页码属于课标级公共信息，可以入库。
- 自用（孩子学自己正在读的教材）没问题；若要分享或上架，**不得包含教材正文**。

## 安全说明

- 本应用**没有任何鉴权**，设计前提是只在家中局域网内使用。
- **不要把 8000 端口转发到公网**，不要做端口映射或内网穿透。
- 数据库端口没有发布到宿主机，只有 `api` 容器能连。

## 开发

```bash
# 后端测试（推荐用法：一条命令，自带 pytest，不需要手工装依赖）
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test

# 前端模块一致性检查（无构建步骤，靠这个脚本兜底）
node scripts/check-imports.mjs

# 前端纯函数测试（标签文案、日期格式化等）
node scripts/test-ui-pure.mjs

# 前端视图接线测试（含最小 DOM 垫片 + 静态接线检查）
node scripts/test-views.mjs

# 复习并发压测（真线程，验证 SRS 状态不丢更新）
python scripts/stress_review.py 20 5

# 完整验收（逐条跑 spec §10 的标准，需容器在跑）
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  run --rm --entrypoint sh test -c "cd /app && PYTHONPATH=src python /app/scripts/acceptance.py"
```

> 原生 ES module 没有编译器兜底，**接线错误**（例如把空数组传给视图构造函数，
> 事后再想用赋值补上——赋值赋在返回对象上，是死代码）纯函数测试和 import
> 检查都发现不了。`test-views.mjs` 用一个最小 DOM 垫片把真实视图逻辑跑起来，
> 并附带静态检查防止这类写法回潮。

测试跑在**同一个数据库**上（库表由同一套迁移创建），因此夹具必须在 teardown 里
彻底清理：错题要物理删除（API 只能软删除），知识点要删掉。验证方式是跑测试前后
比对 `COUNT(*)` —— 数字必须完全一致。详见 `app/tests/conftest.py` 的说明。

> `app/Dockerfile` 是运行镜像，刻意不含 pytest（保持精简）。
> 测试依赖在 `Dockerfile.test` 里叠加，这样测试环境可复现，
> 不必每次 `exec` 进去 `pip install`。

改前端（`web/`）和后台模板不需要重建镜像——compose 用挂载覆盖了镜像内副本，
刷新浏览器即可。迁移文件（`app/migrations/`）同样挂了载，新增迁移后
**重启**容器即可生效，不必重建镜像：

```bash
docker compose restart api
```

改后端 Python 需要重建：

```bash
docker compose up -d --build api
```

## 架构

```
平板浏览器 ──HTTP/局域网──> api 容器(FastAPI :8000)
                             ├─ /            静态前端（原生 ES module，无构建）
                             ├─ /admin       服务端渲染后台（Jinja2）
                             ├─ /api/*       JSON 接口
                             └─ /uploads/*   题目原图
                                    │
                                    ├──> db 容器(Postgres 16)
                                    └──> fq-dgx-01:8888 (deepseek-v4.1-flash)
```

**一条关键设计边界**：所有统计数字由 SQL 算好，模型只负责把数字解释成人话。
绝不让模型自己去数错题——那是它最容易出错且我们无法察觉的用法。