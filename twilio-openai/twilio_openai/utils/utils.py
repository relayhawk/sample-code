def load_system_message():
    with open("twilio_openai/prompts/system_prompt.md", "r") as f:
        return f.read().strip()


LOG_EVENT_TYPES = [
    'response.content.done', 'rate_limits.updated', 'response.done',
    'input_audio_buffer.committed', 'input_audio_buffer.speech_stopped',
    'input_audio_buffer.speech_started', 'session.created'
]
