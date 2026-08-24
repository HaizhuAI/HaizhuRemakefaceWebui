# RemakeFace Pro 生图工作站 (DARKROOM LAB)

逆向 `RemakeFace Ai.apk`（v1.7.7 PRO 破解版，`com.photoeditor.remakemefaceswapaigenerator`）后端协议后
重新封装的可部署生图工作站：

- **WebUI**：HaizhuDesignSkill 深色工作台（Dark AI workspace：电光蓝单强调 + 三层表面分层 + 克制动效），底部固定生图输入栏 + 历史画廊，桌面 / 移动端自适应，管理员密码访问
- **9 个 PRO 模型全部解锁**（实时 config 下发，`priceCredit=0`，不受限制）
- **OpenAI 兼容 API 网关**：`/v1/models` + `/v1/images/generations`，Bearer Token 鉴权
- **能力**：文生图 / 图生图（参考图）/ 人脸替换（faceswap）/ 多图混合（multifaceswap）/ 人脸检测 / NSFW 检测

---

## 一、快速开始

```bash
# 1) 依赖
pip install -r requirements.txt            # fastapi uvicorn python-multipart pycryptodome Pillow

# 2) 配置（可选）
cp .env.example .env                      # 改 ADMIN_PASSWORD / GATEWAY_TOKEN / SESSION_SECRET

# 3) 启动
bash start.sh                             # 默认 0.0.0.0:8611
# 或
python3 -m uvicorn gateway.app:app --host 0.0.0.0 --port 8611
```

浏览器打开 `http://<host>:8611` → 输入管理员密码（默认 `admin123`）进入工作站。

### 界面说明（HaizhuDesignSkill · Dark AI workspace）

- 顶部：固定导航（实色表面 + 边框分层，无玻璃拟态），RF 徽标 + 在线状态 + 文生图/换脸切换 + 退出
- 底部：悬浮生图输入栏（提示词 + 模型/比例/数量/参考图参数），蓝底生成按钮
- 主体：历史任务画廊（响应式网格），支持搜索、状态筛选、点击大图预览（左右切换/下载/新窗口）
- 换脸：切换顶部分页后上传源图/目标图，可选增强画质
- 主题：强制深色工作台（`#0a0e14` 底 / `#11161f` 表面 / `#4da3ff` 电光蓝）
- 设计规范：单强调色、三层表面分层、动效只用于状态反馈、`prefers-reduced-motion` 全关、`:focus-visible` 全局描边

### systemd 部署

```bash
sudo mkdir -p /opt/remakeface-webui
sudo cp -r gateway webui data state .env requirements.txt /opt/remakeface-webui/
sudo cp remakeface.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now remakeface
```

### Docker

```bash
docker build -t remakeface-webui .
docker run -d -p 8611:8611 -e ADMIN_PASSWORD=admin123 --name remakeface remakeface-webui
```

---

## 二、配置项（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ADMIN_PASSWORD` | `admin123` | WebUI 管理员登录密码 |
| `GATEWAY_TOKEN` | 自动 | OpenAI 网关 Bearer Token；留空 = `sha256("gateway:"+密码)` 前 48 位 |
| `SESSION_SECRET` | 固定值 | 会话签名密钥，**部署必改** |
| `PORT` | `8611` | 监听端口 |

---

## 三、OpenAI 兼容 API 网关

请求地址：`http://<host>:8611`，鉴权：`Authorization: Bearer <GATEWAY_TOKEN>`。

### 模型列表

```bash
curl -s http://127.0.0.1:8611/v1/models \
  -H "Authorization: Bearer $TOKEN"
```

### 文生图

```bash
curl -s http://127.0.0.1:8611/v1/images/generations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "model": "seedream5.0_pro",
    "prompt": "cyberpunk city, neon rain, cinematic light",
    "n": 1,
    "size": "1024x1024",
    "response_format": "url"
  }'
```

### 图生图（参考图）

```python
import openai  # 任意 OpenAI SDK 指向网关即可
client = openai.OpenAI(base_url="http://127.0.0.1:8611/v1",
                       api_key="<GATEWAY_TOKEN>")
resp = client.images.generate(
    model="banana2",
    prompt="make it a red ferrari",
    n=1,
    size="512x512",
    extra_body={"image": [{"url": "https://.../ref.png"}]},  # 或 data:image/png;base64,...
)
```

`size` 映射：`1024x1024→1:1`、`768x1024→3:4`、`1024x768→4:3`、`896x1152→9:16`、`1152x896→16:9`、`Original→原图比例`。
`response_format` 支持 `url` / `b64_json`。

---

## 四、PRO 模型表（全部解锁 · priceCredit=0）

| 模型 ID | 名称 | 分组 | 说明 |
|---|---|---|---|
| `seedream5.0_pro` | Seedream 5 Pro | 旗舰 | Premium generation, exceptional detail |
| `seedream5.0` | Seedream 5.0 | 旗舰 | Smarter generation, refined detail |
| `seedream4.5` | Seedream 4.5 | 主流 | Sharp, detailed, consistent |
| `seedream4` | Seedream 4.0 | 主流 | Flexible generation & editing |
| `banana_pro` | Nano Banana Pro | 编辑 | Realistic + text-heavy visuals |
| `banana2` | Nano Banana 2 | 编辑 | Fast generation & editing |
| `banana` | Nano Banana | 编辑 | Quick edits & transformations |
| `banana2_lite` | Banana 2 Lite | 轻量 | Lightweight fast generation |
| `gpt_image_2` | GPT Image 2 | 编辑 | Accurate prompts, clean edits |

---

## 五、逆向协议摘要（gateway/client.py 干净室实现）

- 后端：`https://app-remakeme.masyadi.com/`，presign：`https://temp-file.masyadi.com/api/presigned`
- 加密：**AES-128-ECB/PKCS5，密钥 `vOVH6sdmpNWjRRIq`**（自 `ju.smali` 恢复）
  请求体恒为 `{"data": base64(AES(json))}`；config / token / presign 响应加密，create / status 明文
- 任务流：`config → /api/tasks/token（单次有效）→ /api/tasks/create（header task-token）→ 轮询 urlStatusTask`
- 上传：presign 请求 `objectName` 必须是**相对 key**（`ai-generate/<uuid>.png` / `uploads/<uuid>.jpg`），
  返回 `{url, objectName}`，PUT 直传 R2；`image_uri_list` / `sourceImage` 等字段填**返回的 objectName**
  （⚠️ 若传完整 URL 当 objectName，服务端回读参考图会 403 —— 已按 smali `bb4.h`/`n9` 恢复正确格式）
- jobData 结构（smali `m9`/`iy3` 恢复）：
  - `aiImageGen`: `{image_uri_list[], prompt, image_ratio, model, generate_count, peoples?}`
  - `faceswap`: `{sourceImage, targetImage, type: standard|ultra, versi:"v3", isEnhance}`
  - `getManyFaces` / `checkNsfw`: `{typeImge:"path", path_image:<key>, versi:"v3"}`
  - `multifaceswap`: `{imageResourcePaths[], targetImagePath, facesToSwap[], ...}`

---

## 六、测试

```bash
python3 tests/test_api.py                # 鉴权 + 模型 + 网关（无生图，秒级）
TEST_GENERATE=1 python3 tests/test_api.py # 真实文生图 + 图生图（约 2~4 分钟）
python3 tests/visual_check.py            # Playwright 桌面 1440x900 + 移动 390x844 UI 验收
```

---

## 七、目录结构

```
gateway/client.py     # RemakeClient：全协议（config/token/presign/upload/create/poll/6种能力）
gateway/app.py        # FastAPI：WebUI 静态 + 管理 API + OpenAI 兼容网关 + 任务线程池
webui/login.html      # 暗房风格登录页
webui/index.html      # DARKROOM LAB 主界面（模型/比例/参考图/faceswap/历史）
data/                 # 出图缓存 data/generated + 任务记录 jobs.json
state/client_state.json # 会话状态（config/token 缓存）
artifacts/            # UI 验收截图
tests/                # API + 视觉回归
```

## 八、故障排查

- **参考图 403 / 404**：确认走的是修复后的 `upload_image`（相对 objectName），服务重启后生效
- **create 401/403**：task-token 单次有效，client 已自动 force 重取重试
- **faceswap 报 No face detected**：换含清晰人脸的源图/目标图（服务端人脸检测要求）
- **改 gateway/*.py 后不生效**：必须重启 uvicorn（`fuser 8611/tcp` 拿 PID 再 kill）
