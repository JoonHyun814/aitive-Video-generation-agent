# Flux.2 Dev 프롬프트 가이드

## 모델 구성

| 역할 | 모델 |
|---|---|
| UNET | `flux2_dev_fp8mixed.safetensors` (Flux.2 Dev, FP8 Mixed) |
| CLIP | `mistral_3_small_flux2_bf16.safetensors` (Mistral 3 Small) |
| VAE | `full_encoder_small_decoder.safetensors` |
| LoRA | `Flux_2-Turbo-LoRA_comfyui.safetensors` (Turbo 가속) |

Mistral 3 Small 텍스트 인코더를 사용하므로 **자연어 문장형 지시**를 매우 잘 이해합니다.  
태그 나열(`masterpiece, best quality, 1girl`) 방식보다 장면을 설명하는 문장 방식이 훨씬 효과적입니다.

---

## ⚠️ ComfyUI 필수 설정 (Turbo LoRA 전용)

**이 값을 지키지 않으면 이미지가 심하게 깨지거나 타버립니다.**

| 항목 | 권장값 |
|---|---|
| Steps | `8` |
| CFG Scale | `1.0 ~ 2.0` (기본 `1.5`) |
| Sampler | `euler` |
| Scheduler | `sgm_uniform` 또는 `simple` |

> Turbo LoRA는 8스텝으로 고품질 이미지를 완성하도록 설계되었습니다.  
> 스텝을 20~40으로 높이지 마세요.

---

## 1. 황금률 — 순서와 구체성

### 순서가 가장 중요합니다 (Order matters)

Flux.2는 **프롬프트의 맨 앞에 가장 큰 가중치**를 둡니다.  
가장 표현하고 싶은 핵심 주제를 무조건 문장 맨 앞에 배치하세요.

```
❌ 나쁜 예
"A detailed background of a cyberpunk city, neon lights, raining, and there is a girl..."

✅ 좋은 예
"A young woman standing in a rainy cyberpunk city, illuminated by neon lights..."
```

---

### 구체성이 생명입니다 (Specificity wins)

"Awesome", "cool scene" 같은 모호한 표현 대신 **정확한 용어**를 사용하세요.  
Flux.2는 구조상 **네거티브 프롬프트를 사용하지 않습니다.** 없애고 싶은 것을 `--no text`처럼 쓰는 대신, 화면에 있어야 할 것만 정확히 묘사하세요.

```
❌ 나쁜 예
"Portrait, no extra fingers, no text"

✅ 좋은 예
"Clean headshot portrait, hands out of frame, minimal background"
```

---

## 2. 정확한 텍스트 / 로고 렌더링

Flux.2의 가장 큰 강점 중 하나는 **이미지 안에 텍스트를 정확하게 그려내는 능력**입니다.

- 출력할 글씨는 반드시 **따옴표(`'` 또는 `"`)**로 묶어주세요.
- 폰트 스타일, 위치, 색상을 상세히 지정하면 정확하게 반영됩니다.

```
"The text 'OPEN' appears in red neon letters above the door."
→ 문 위에 빨간색 네온 글씨로 'OPEN' 렌더링

"The logo text 'ACME' in elegant serif typography, color #FF5733."
→ #FF5733 색상, 세리프 서체의 'ACME' 로고
```

---

## 3. 실사(Photorealism) 카메라 제어

조리개 값(f-stop)과 조명을 명시하면 심도(아웃포커싱)를 자유롭게 제어할 수 있습니다.

### 조리개 (심도 제어)

| f값 | 효과 | 용도 |
|---|---|---|
| `f/1.4` ~ `f/2.8` | 얕은 심도 — 배경 흐림 | 인물 클로즈업, 제품 촬영 |
| `f/4` ~ `f/5.6` | 중간 심도 — 배경과 피사체 균형 | 환경 포함 인물 촬영 |
| `f/8` ~ `f/16` | 깊은 심도 — 전체 선명 | 풍경, 건축, 광각 |

### 조명 / 재질 키워드

| 키워드 | 설명 |
|---|---|
| `soft diffused studio lighting` | 부드러운 확산 스튜디오 조명 |
| `golden hour backlighting` | 골든 아워 역광 |
| `dramatic rim lighting` | 드라마틱한 윤곽 조명 |
| `hard directional sunlight` | 강한 방향성 햇빛 |
| `overcast flat lighting` | 흐린 날 균일한 조명 |
| `matte ceramic` | 무광 세라믹 재질 |
| `glossy metallic surface` | 유광 금속 표면 |

---

## 4. 프롬프트 작성 공식

```
[핵심 피사체/주제]
+ [행동/포즈]
+ [배경 맥락/위치]
+ [스타일, 재질, 조명]
+ [카메라 설정 (f값, 렌즈 등)]
+ [텍스트 묘사 (필요한 경우)]
```

### 예시

```
A young woman in a white lab coat
standing at a futuristic holographic control panel,
inside a sleek sci-fi research facility with glass walls,
soft blue ambient lighting, matte metallic surfaces,
shot on Sony A7R with 85mm lens, f/2.0
```

```
An old leather-bound book lying open on a wooden desk,
pages yellowed with age, a burning candle beside it,
warm candlelight, shallow depth of field, f/1.8,
the cover reads 'THE FORGOTTEN ARCHIVE' in embossed gold lettering
```

```
A close-up product shot of a glass perfume bottle,
the label reads 'LUMIÈRE' in thin sans-serif gold type,
placed on a white marble surface,
soft diffused studio lighting, f/8, minimal shadow
```

---

## 5. API 사용 예시

```python
import requests

SERVER = "http://10.110.56.116:8001"

resp = requests.post(f"{SERVER}/flux/generate",
    data={
        "positive_prompt": (
            "A young woman standing in a rainy cyberpunk city at night, "
            "illuminated by neon signs reflecting on wet pavement, "
            "wearing a translucent raincoat, dramatic rim lighting, "
            "shot on 35mm film, f/2.0, shallow depth of field"
        ),
        "width": 1360,
        "height": 768,
    },
)

prompt_id = resp.json()["prompt_id"]

result = requests.get(f"{SERVER}/result/{prompt_id}/wait",
                      params={"timeout_s": 600}).json()

url = result["outputs"][0]["url"].replace("127.0.0.1", "10.110.56.116")
print("출력:", url)
```

---

## 6. 팁 & 주의사항

- **네거티브 프롬프트 무시**: Flux 구조상 네거티브 프롬프트 필드는 비워두거나 API의 기본값을 그대로 사용하세요. 억제 표현(`no hands`, `--no text`)은 효과가 없습니다.
- **해상도 배수**: 가로/세로를 64의 배수로 지정하세요. 권장: `1024×1024`, `1360×768`, `768×1360`, `1024×576`.
- **Turbo 스텝 고정**: 8스텝 이상으로 올리지 마세요. 스텝을 높이면 과포화/과노출(overcooked) 현상이 발생합니다.
- **텍스트 렌더링**: 이미지 안에 글씨를 넣을 때는 반드시 따옴표로 감싸야 정확하게 렌더링됩니다.
- **소요 시간**: Turbo LoRA 덕분에 1024×1024 기준 약 6분 내외입니다.
