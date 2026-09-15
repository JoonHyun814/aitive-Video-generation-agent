# Qwen Image Edit 프롬프트 가이드

## 모델 구성

| 역할 | 모델 |
|---|---|
| UNET | `qwen_image_edit_2511_bf16.safetensors` |
| CLIP | `qwen_2.5_vl_7b_fp8_scaled.safetensors` (Qwen 2.5 VL 7B) |
| VAE | `qwen_image_vae.safetensors` |
| LoRA (기본) | `qwen-image-edit-2511-multiple-angles-lora.safetensors` |
| LoRA (Lightning) | `Qwen-Image-Lightning-8steps-V2.0-bf16.safetensors` |

---

## 1. 다중 각도 LoRA (Multiple Angles LoRA) 프롬프팅

가우시안 스플래팅 데이터로 학습된 LoRA로, **4가지 고도 × 8가지 방위각 × 3가지 거리 = 96가지 카메라 포즈**를 지원합니다.

### 프롬프트 공식

```
[방위각] + [고도] + [거리(선택)]
```

---

### A. 방위각 (Azimuth) — 바라보는 방향

| 키워드 | 설명 |
|---|---|
| `front view` | 정면 |
| `front-right quarter view` | 우측 반측면 |
| `right side view` | 우측면 |
| `back-right quarter view` | 우측 후면 |
| `back view` | 후면 |
| `back-left quarter view` | 좌측 후면 |
| `left side view` | 좌측면 |
| `front-left quarter view` | 좌측 반측면 |

---

### B. 고도 (Elevation) — 카메라 높이

| 키워드 | 각도 | 설명 |
|---|---|---|
| `low-angle shot` | -30° | 아래에서 위를 올려다보는 앵글 |
| `eye-level shot` | 0° | 눈높이 |
| `elevated shot` | +30° | 약간 위에서 내려다보는 앵글 |
| `high-angle shot` | +60° | 높은 곳에서 내려다보는 앵글 |

---

### C. 거리 (Distance)

| 키워드 | 설명 |
|---|---|
| `close-up` | 클로즈업 (근접) |
| `medium shot` | 미디엄 샷 |
| `full body` | 전신 |

---

### 조합 예시

```
front view low-angle shot close-up
→ 정면, 아래에서 위로 올려다본 클로즈업

right side view eye-level shot
→ 우측면, 눈높이

back-left quarter view high-angle shot close-up
→ 좌측 후면, 위에서 내려다본 클로즈업

front-right quarter view elevated shot full body
→ 우측 반측면, 약간 위에서 바라본 전신샷
```

> **주의**: 방위각 키워드는 LoRA 학습 데이터 기준 표현을 정확히 사용해야 합니다.  
> 예를 들어 `"right front"` 대신 `"front-right quarter view"` 와 같이 위 표의 키워드를 그대로 입력하세요.

---

## 2. 일반 이미지 편집 프롬프팅

Qwen 2.5 VL 7B 텍스트 인코더를 사용하므로 **태그 나열이 아닌 자연어 지시문** 방식이 효과적입니다.

### 유형별 예시

**배경 변경**
```
Add a beautiful sunset sky in the background with warm orange and pink colors
Change the background to a futuristic neon-lit city at night
Replace the background with a snowy mountain landscape
```

**의상 / 오브젝트 변경**
```
Change the character's shirt to a red leather jacket
Replace the hat with a black baseball cap
Add a pair of sunglasses to the character
```

**포즈 / 동작 변경**
```
Edit to have her raise up the object with both hands
Make the character look to the left
Have the character sit down on the ground
```

**스타일 변환**
```
Render in the style of a cinematic oil painting
Make it look like a photorealistic 3D render
Convert to a dark fantasy illustration style
```

**조명 / 분위기 변경**
```
Add dramatic rim lighting from the left side
Apply warm golden-hour lighting
Change to a cold blue moonlit atmosphere
```

---

## 3. API 요청 시 추천 설정

### 기본 설정 (BF16 모델 + Multiple Angles LoRA)

| 항목 | 권장값 |
|---|---|
| CFG Scale | `4.0` |
| Steps | `40` |
| LoRA Strength | `0.8 ~ 1.0` |

### Lightning LoRA 사용 시 (8-step 고속 모드)

Lightning LoRA(`Qwen-Image-Lightning-8steps-V2.0-bf16.safetensors`)가 활성화된 경우:

| 항목 | 권장값 |
|---|---|
| CFG Scale | `1.0` |
| Steps | `8` |
| LoRA Strength | `1.0` |

> 현재 API(`/qwen-edit/generate`)는 기본 40-step 설정으로 고정되어 있습니다.

---

## 4. API 사용 예시

```python
import requests

SERVER = "http://10.110.56.116:8001"

# 다중 각도 예시 — 우측면 눈높이 뷰
with open("character.png", "rb") as f:
    resp = requests.post(f"{SERVER}/qwen-edit/generate",
        data={
            "positive_prompt": "right side view eye-level shot close-up, same character, clean background",
            "negative_prompt": "blurry, distorted, watermark, extra limbs",
        },
        files=[("images", ("character.png", f, "image/png"))],
    )

prompt_id = resp.json()["prompt_id"]

# 결과 대기 (~8~10분)
result = requests.get(f"{SERVER}/result/{prompt_id}/wait",
                      params={"timeout_s": 1200}).json()

url = result["outputs"][0]["url"].replace("127.0.0.1", "10.110.56.116")
print("출력:", url)
```

```python
# 이미지 2장 조합 — 두 캐릭터를 한 장면으로
with open("char1.png", "rb") as f1, open("char2.png", "rb") as f2:
    resp = requests.post(f"{SERVER}/qwen-edit/generate",
        data={
            "positive_prompt": "Combine these two characters into a single cinematic scene, "
                               "facing each other, dramatic lighting, film still",
        },
        files=[
            ("images", ("char1.png", f1, "image/png")),
            ("images", ("char2.png", f2, "image/png")),
        ],
    )
```

---

## 5. 팁 & 주의사항

- **각도 키워드 정확성**: Multiple Angles LoRA는 학습 데이터의 정확한 키워드에 반응합니다. 위 표에 없는 변형 표현(예: `"side view"` 대신 `"right side view"`)은 각도가 불안정할 수 있습니다.
- **참조 이미지 품질**: 해상도가 너무 낮거나 배경이 복잡한 참조 이미지는 일관성 저하로 이어질 수 있습니다.
- **이미지 2~3장 활용**: 정면 + 측면 이미지를 함께 입력하면 캐릭터 일관성이 향상됩니다.
- **부정 프롬프트**: `"blurry, distorted, extra limbs, watermark, low quality"` 정도가 일반적으로 효과적입니다, 실사 이미지를 생성할때에는 `"plastic, smooth skin, airbrushed, 3d render, CGI, cartoon, illustration, perfect symmetry, overexposed"` 가 효과적입니다.
- **출력 해상도**: 워크플로 내부에서 입력 이미지 해상도에 맞춰 자동 조정됩니다 (기본 1472×1104 내외).
