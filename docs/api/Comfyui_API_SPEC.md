# ComfyUI Workflow API 명세서

## 개요

로컬 머신(ComfyUI 설치 서버)에서 실행되는 REST API 서버입니다.  
외부 컴퓨터에서 HTTP 요청으로 아래 세 가지 워크플로를 실행할 수 있습니다.

| 워크플로 | 엔드포인트 | 출력 |
|---|---|---|
| Flux | `POST /flux/generate` | 이미지 (PNG) |
| Qwen Edit | `POST /qwen-edit/generate` | 이미지 (PNG) |
| MiniMax H3 | `POST /minimax/generate` | 영상 (MP4) |

---

## 서버 실행 방법 (ComfyUI 서버 머신)

```bat
:: 1. ComfyUI 먼저 실행
run_network.bat

:: 2. API 서버 실행 (별도 터미널)
run_api.bat
```

- **API 포트**: `8001`
- **ComfyUI 포트**: `8188` (API 서버가 내부적으로 사용)
- **Swagger UI**: `http://<서버IP>:8001/docs`

---

## 공통 사항

### 요청 형식
- 모든 generate 엔드포인트는 `multipart/form-data` 형식으로 요청합니다.
- 이미지 파일은 `File` 필드로 첨부합니다.

### 응답 패턴 (비동기 2단계)

생성 요청은 즉시 `prompt_id`를 반환하고, 실제 처리는 ComfyUI 큐에서 순차 진행됩니다.

```
POST /*/generate  →  { "prompt_id": "..." }  (즉시)
                          ↓
GET /result/{id}/wait  →  { "status": "success", "outputs": [...] }  (완료 시)
```

또는 짧은 주기로 폴링:
```
GET /result/{id}  →  { "status": "pending" | "success" | "error", ... }
```

### 결과 응답 구조

```json
{
  "prompt_id": "a1b2c3d4-0000-0000-0000-000000000000",
  "status": "success",
  "outputs": [
    {
      "url": "http://127.0.0.1:8188/api/view?filename=Flux2_dev_00001_.png&type=output",
      "filename": "Flux2_dev_00001_.png",
      "subfolder": "",
      "type": "output",
      "media_type": "image/png"
    }
  ],
  "error": ""
}
```

| 필드 | 설명 |
|---|---|
| `status` | `"pending"` / `"success"` / `"error"` |
| `outputs[].url` | ComfyUI 서버 기준 다운로드 URL (`127.0.0.1` 포함) |
| `outputs[].filename` | 저장된 파일명 |
| `outputs[].subfolder` | 서브폴더 (Qwen: `qwen_edit/`, MiniMax: `video/`, Flux: 빈 문자열) |
| `outputs[].media_type` | `"image/png"` 또는 `"video/mp4"` (파일 확장자 기반) |

> **주의**: `outputs[].url`의 호스트가 `127.0.0.1`로 반환됩니다.  
> 외부 서버에서 다운로드할 때는 실제 서버 IP로 치환해야 합니다.

### 출력 파일 다운로드

```python
SERVER_IP = "10.110.56.116"  # ComfyUI 서버 실제 IP

url = result["outputs"][0]["url"]
url = url.replace("127.0.0.1", SERVER_IP)

import requests
data = requests.get(url).content

ext = ".mp4" if result["outputs"][0]["media_type"] == "video/mp4" else ".png"
with open("output" + ext, "wb") as f:
    f.write(data)
```

---

## 1. Flux — 텍스트 → 이미지

### `POST /flux/generate`

**출력 파일명 패턴**: `Flux2_dev_00001_.png`

#### 요청 파라미터 (form-data)

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `positive_prompt` | string | ✅ | — | 생성할 이미지 설명 |
| `negative_prompt` | string | ❌ | `""` | Flux 모델 특성상 무시됨 (API 일관성용으로만 존재) |
| `width` | int | ❌ | `1024` | 이미지 가로 픽셀 |
| `height` | int | ❌ | `1024` | 이미지 세로 픽셀 |
| `seed` | int | ❌ | random | 동일 결과 재현용 시드 |

#### 예시 (curl)

```bash
curl -X POST http://10.110.56.116:8001/flux/generate \
  -F "positive_prompt=a cinematic photo of a mountain at golden hour, 8K" \
  -F "width=1360" \
  -F "height=768"
```

#### 예시 (Python)

```python
import requests

SERVER = "http://10.110.56.116:8001"

resp = requests.post(f"{SERVER}/flux/generate", data={
    "positive_prompt": "a cinematic photo of a mountain at golden hour, 8K",
    "width": 1360,
    "height": 768,
})
prompt_id = resp.json()["prompt_id"]

# Flux는 약 5~8분 소요 — timeout 넉넉히
result = requests.get(f"{SERVER}/result/{prompt_id}/wait",
                      params={"timeout_s": 600}).json()

url = result["outputs"][0]["url"].replace("127.0.0.1", "10.110.56.116")
print(url)
```

---

## 2. Qwen Edit — 참조 이미지 기반 이미지 편집

### `POST /qwen-edit/generate`

참조 이미지 1~3장을 업로드하고 프롬프트로 편집 지시를 내립니다.

**출력 파일명 패턴**: `qwen_edit/image_enhanced_1472x1104_00001_.png`

#### 요청 파라미터 (form-data)

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `positive_prompt` | string | ✅ | — | 편집 지시 프롬프트 |
| `negative_prompt` | string | ❌ | (기본 네거티브) | 원하지 않는 요소 |
| `seed` | int | ❌ | random | 재현용 시드 |
| `images` | File[] | ✅ | — | 참조 이미지 1~3장 (필드명 `images` 반복 전송) |

> 이미지 3장 초과 시 앞의 3장만 사용됩니다.

#### 예시 (curl)

```bash
# 이미지 1장
curl -X POST http://10.110.56.116:8001/qwen-edit/generate \
  -F "positive_prompt=change the background to a futuristic city at night" \
  -F "negative_prompt=blurry, low quality, artifacts" \
  -F "images=@image1.jpg"

# 이미지 2장
curl -X POST http://10.110.56.116:8001/qwen-edit/generate \
  -F "positive_prompt=blend these two characters into one scene" \
  -F "images=@image1.jpg" \
  -F "images=@image2.jpg"
```

#### 예시 (Python)

```python
import requests

SERVER = "http://10.110.56.116:8001"

with open("image1.jpg", "rb") as f1, open("image2.jpg", "rb") as f2:
    resp = requests.post(f"{SERVER}/qwen-edit/generate",
        data={
            "positive_prompt": "make it look like a cinematic oil painting",
            "negative_prompt": "bad quality, artifacts, watermark",
        },
        files=[
            ("images", ("image1.jpg", f1, "image/jpeg")),
            ("images", ("image2.jpg", f2, "image/jpeg")),
        ],
    )

prompt_id = resp.json()["prompt_id"]

# Qwen Edit는 약 8~15분 소요
result = requests.get(f"{SERVER}/result/{prompt_id}/wait",
                      params={"timeout_s": 1200}).json()

url = result["outputs"][0]["url"].replace("127.0.0.1", "10.110.56.116")
print(url)
```

---

## 3. MiniMax H3 — 참조 이미지 기반 영상 생성

### `POST /minimax/generate`

첫 프레임 참조 이미지와 컷별 키프레임 이미지(0~3개)로 영상을 생성합니다.

**출력 파일명 패턴**: `video/MiniMax_H3_00001_.mp4`

#### 요청 파라미터 (form-data)

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `prompt` | string | ✅ | — | 영상 장면/내용 설명 |
| `total_duration` | float | ❌ | `7.0` | 영상 총 길이(초), 최대 약 15초 |
| `megapixels` | float | ❌ | `0.4` | 해상도 (0.2~0.98 MP) |
| `aspect_ratio` | string | ❌ | `"16:9 (Widescreen)"` | 화면 비율 (아래 표 참조) |
| `cut_times` | string | ❌ | `"[]"` | 컷 타임스탬프 JSON 배열 (초 단위), e.g. `"[1.5, 3.0]"` |
| `seed` | int | ❌ | random | 재현용 시드 |
| `reference_image` | File | ✅ | — | 첫 프레임 기준 참조 이미지 |
| `cut_images` | File[] | 조건부 | `[]` | 각 컷의 키프레임 이미지 (순서대로, `cut_times` 개수와 반드시 일치) |

> `cut_times`와 `cut_images` 개수가 다르면 `422` 에러가 반환됩니다.  
> 컷 없이 참조 이미지만 사용하려면 `cut_times="[]"`, `cut_images` 생략.

#### `aspect_ratio` 선택값

| 값 | 비율 |
|---|---|
| `"16:9 (Widescreen)"` | 가로형 (기본) |
| `"9:16 (Portrait)"` | 세로형 (모바일) |
| `"1:1 (Square)"` | 정방형 |
| `"4:3 (Standard)"` | 표준 |
| `"21:9 (Cinematic)"` | 시네마틱 |

#### 예시 (curl) — 참조 이미지만

```bash
curl -X POST http://10.110.56.116:8001/minimax/generate \
  -F "prompt=A serene lake at dawn, gentle ripples, birds flying overhead" \
  -F "total_duration=7.0" \
  -F "cut_times=[]" \
  -F "reference_image=@ref.png"
```

#### 예시 (curl) — 컷 2개

```bash
curl -X POST http://10.110.56.116:8001/minimax/generate \
  -F "prompt=A hero running through a burning building, dramatic escape" \
  -F "total_duration=10.0" \
  -F "cut_times=[2.0, 6.0]" \
  -F "reference_image=@ref.png" \
  -F "cut_images=@cut1.png" \
  -F "cut_images=@cut2.png"
```

#### 예시 (Python)

```python
import requests, json

SERVER = "http://10.110.56.116:8001"

with open("ref.png", "rb") as ref, \
     open("cut1.png", "rb") as c1, \
     open("cut2.png", "rb") as c2:

    resp = requests.post(f"{SERVER}/minimax/generate",
        data={
            "prompt": "dramatic chase scene through neon-lit streets, rain, cinematic",
            "total_duration": 10.0,
            "aspect_ratio": "16:9 (Widescreen)",
            "cut_times": json.dumps([2.5, 6.0]),
        },
        files=[
            ("reference_image", ("ref.png",  ref, "image/png")),
            ("cut_images",      ("cut1.png", c1,  "image/png")),
            ("cut_images",      ("cut2.png", c2,  "image/png")),
        ],
    )

prompt_id = resp.json()["prompt_id"]

# MiniMax 영상은 10~25분 소요 — 폴링 방식 권장
import time
while True:
    r = requests.get(f"{SERVER}/result/{prompt_id}").json()
    if r["status"] == "success":
        url = r["outputs"][0]["url"].replace("127.0.0.1", "10.110.56.116")
        print("완료:", url)
        break
    elif r["status"] == "error":
        print("실패:", r["error"])
        break
    print("처리 중...")
    time.sleep(30)
```

---

## 결과 폴링 엔드포인트

### `GET /result/{prompt_id}` — 논블로킹 폴링

즉시 응답합니다. 아직 완료되지 않았으면 `status: "pending"` 반환.

```bash
curl http://10.110.56.116:8001/result/a1b2c3d4-...
```

### `GET /result/{prompt_id}/wait` — 완료 대기 (블로킹)

서버 내부에서 3초 간격으로 폴링하다가 완료되면 반환합니다.

| 파라미터 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `timeout_s` | float | `1800` | 최대 대기 시간(초) |

```bash
curl "http://10.110.56.116:8001/result/a1b2c3d4-.../wait?timeout_s=1200"
```

timeout 초과 시 `status: "pending"`, `error: "Timed out after Ns"` 반환.

> **장시간 작업 주의**: MiniMax처럼 10분 이상 걸리는 경우, HTTP 클라이언트/프록시의 자체 timeout에 의해 연결이 먼저 끊길 수 있습니다. 이 경우 `/result/{id}` 폴링 방식을 사용하세요.

---

## 헬스 체크

```bash
curl http://10.110.56.116:8001/health
# {"api": "ok", "comfyui": "ok"}
```

`comfyui: "unreachable"` 이면 ComfyUI(`run_network.bat`)가 실행 중이지 않은 것입니다.

---

## 실측 소요 시간

RTX PRO 6000 Blackwell (102GB VRAM) 기준, **큐 대기 시간 미포함**.

| 워크플로 | 조건 | 소요 시간 |
|---|---|---|
| Flux | 1024×1024, dev fp8 모델 | 약 6분 |
| Qwen Edit | 1472×1104, 이미지 1장 | 약 8~10분 |
| MiniMax H3 | 7초, 컷 없음, 0.4MP | 약 10~20분 |

> 요청은 ComfyUI 큐에 순차로 처리됩니다. 앞선 요청이 있으면 대기 시간이 추가됩니다.

---

## 제약 사항

| 항목 | 제한 |
|---|---|
| Flux 네거티브 프롬프트 | 모델 특성상 무시됨 |
| Qwen 참조 이미지 수 | 최대 3장 (초과 시 앞 3장만 사용) |
| MiniMax 컷 수 | 최대 3개 |
| MiniMax 영상 길이 | 최대 약 15초 |
| 동시 처리 | 1개 (ComfyUI 큐 순차 처리) |
| 출력 URL 호스트 | 항상 `127.0.0.1` — 다운로드 시 실제 서버 IP로 치환 필요 |

---

## 권장 `timeout_s` 값

| 워크플로 | `/wait` 권장 timeout |
|---|---|
| Flux | `600` (10분) |
| Qwen Edit | `1200` (20분) |
| MiniMax H3 | 폴링 방식 권장, `/wait` 사용 시 `1800` (30분) |
