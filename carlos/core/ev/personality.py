CHOICES = {
    'response_length': {'minimal', 'normal', 'detailed'},
    'tone': {'calm', 'natural', 'professional', 'custom'},
    'working_verbosity': {'silent', 'minimal', 'conversational'},
    'acknowledgements': {'off', 'important_only', 'normal'},
    'technical_language': {'simple', 'balanced', 'technical'},
    'humor': {'off', 'light', 'normal'},
    'sarcasm': {'off', 'light'},
    'proactive_speech_threshold': {'off', 'high', 'emergency'},
    'name_usage': {'rare', 'normal', 'often'},
}
RANGES = {'voice_expressiveness': (0.1, 1.0), 'speaking_rate': (0.65, 1.5)}


def snapshot(config):
    return {**config.get('personality', {}),
            'speaking_rate': config.get('voice', {}).get('tts', {}).get('speaking_rate', 1.0)}


def update_values(config, payload):
    unknown = set(payload) - (set(CHOICES) | set(RANGES))
    if unknown or not payload:
        raise ValueError('invalid personality setting: ' + (', '.join(sorted(unknown)) or 'none supplied'))
    updated = snapshot(config)
    for key, value in payload.items():
        if key in RANGES:
            low, high = RANGES[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f'{key} must be between {low} and {high}')
            updated[key] = round(float(value), 2)
        else:
            normalized = str(value).casefold()
            if normalized not in CHOICES[key]:
                raise ValueError(f'invalid {key} value')
            updated[key] = normalized
    return updated


def delivery_instructions(personality):
    humor = {
        'off': 'Skip jokes and playful asides.',
        'light': 'A brief playful aside is fine when it fits; do not force jokes.',
        'normal': 'Use occasional conversational humor when it fits, without making every reply a joke.',
    }.get(personality.get('humor', 'light'))
    sarcasm = ('Keep sarcasm off.' if personality.get('sarcasm', 'off') != 'light'
               else 'Light situational sarcasm is fine; never mock the user or obscure a failure.')
    name = {
        'rare': "Use the user's supplied name sparingly; do not invent a name.",
        'normal': "Use the user's supplied name occasionally when natural; do not invent a name.",
        'often': "Use the user's supplied name when natural, without repeating it in every sentence; do not invent a name.",
    }.get(personality.get('name_usage', 'rare'))
    progress = {
        'silent': 'Skip progress chatter; still report requested results, errors and approval needs.',
        'minimal': 'Keep progress acknowledgements brief and avoid repeated status chatter.',
        'conversational': 'Brief progress explanations are welcome when useful; do not narrate every tool field.',
    }.get(personality.get('working_verbosity', 'minimal'))
    return [line for line in (humor, sarcasm, name, progress) if line]
