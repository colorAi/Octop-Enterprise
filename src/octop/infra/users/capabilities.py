"""Company services represented as explicitly assignable employee resources."""

from typing import Any

IMAGE_CAPABILITY = "capability:generate_image"
VIDEO_CAPABILITY = "capability:generate_video"
STT_CAPABILITY = "capability:speech_to_text"
TTS_CAPABILITY = "capability:text_to_speech"
SEARCH_CAPABILITY = "capability:web_search"

# Stable resource IDs keep grants intact when provider credentials or routes change.
CAPABILITIES = {
    IMAGE_CAPABILITY: (
        "image_generation",
        "company-image-generation",
        "公司生图",
        "生成或编辑图片",
        "company_generate_image",
        "generate_image",
    ),
    VIDEO_CAPABILITY: (
        "video_generation",
        "company-video-generation",
        "公司生视频",
        "根据文字或参考图片生成视频",
        "company_generate_video",
        "generate_video",
    ),
    STT_CAPABILITY: (
        "speech_to_text",
        "company-speech-to-text",
        "公司语音识别",
        "将语音转换为文字",
        "company_transcribe_audio",
    ),
    TTS_CAPABILITY: (
        "text_to_speech",
        "company-text-to-speech",
        "公司语音合成",
        "将文字转换为语音",
        "company_synthesize_speech",
    ),
    SEARCH_CAPABILITY: (
        "web_search",
        "company-web-search",
        "公司联网搜索",
        "通过公司配置的搜索引擎检索网络资料",
        "company_web_search",
        "tavily_search",
        "brave_search",
        "google_search",
        "kimi_search",
        "searchfree_search",
    ),
}
TOOL_CAPABILITIES = {tool: identity for identity, item in CAPABILITIES.items() for tool in item[4:]}


def capability_records() -> list[dict[str, Any]]:
    return [
        {
            "id": identity,
            "slug": item[1],
            "name": item[2],
            "description": f"使用管理员配置的公司服务{item[3]}。需要管理员单独授权。",
            "category": "company-capabilities",
            "kind": "capability",
            "source_name": "company",
            "expert_ids": [],
            "_package_id": None,
            "_agent_id": None,
            "_path": None,
        }
        for identity, item in CAPABILITIES.items()
    ]


def capability_content(identity: str) -> bytes:
    _, slug, name, description, *tools = CAPABILITIES[identity]
    return (
        f"---\nname: {slug}\ndescription: {name}：{description}\ncategory: company-capabilities\n---\n"
        f"Use an available company tool: {', '.join(tools)}. "
        "Credentials stay on the company server. Use only tools granted to this task. "
        "For voice input or reading chat replies aloud, use the application's voice controls. "
        "For media editing, pass the original project images as references. Save outputs "
        "inside the employee's current project, preferably under generated/. Only report "
        "success after the tool returns a verified saved artifact. If unavailable or revoked, "
        "explain the error; do not bypass authorization with scripts or direct provider requests.\n"
    ).encode()
