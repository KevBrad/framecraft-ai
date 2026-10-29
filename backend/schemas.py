import re
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class AnalysisSettings(StrictModel):
    analysis_version: Literal[1,3] = 3
    min_duration: float = Field(default=15, ge=3, le=180)
    max_duration: float = Field(default=45, ge=3, le=180)
    topic: str = Field(default='', max_length=500)

    requested_clips: int = Field(default=10, ge=1, le=20)
    discovery_mode: Literal['viral_moments','educational_insights','emotional_moments','funny_moments','strong_opinions','best_highlights','custom_prompt'] = 'viral_moments'
    custom_prompt: str = Field(default='', max_length=500)
    model_mode: Literal['balanced','quality'] = 'balanced'
    contextual_emojis: bool = True
    emoji_frequency: Literal['off','subtle','balanced','frequent'] = 'balanced'
    animated_captions: bool = True
    keyword_highlights: bool = True
    smart_reframing: bool = True
    output_resolution: Literal['720p','1080p'] = '720p'
    intensity: Literal['minimal','balanced','energetic'] = 'balanced'

    @model_validator(mode='after')
    def ordered(self):
        if self.max_duration < self.min_duration:
            raise ValueError('Maximum duration must be at least minimum duration.')
        return self


class URLImport(AnalysisSettings):
    url: str = Field(min_length=1, max_length=8192)
    title: str = Field(default='', max_length=200)
    rights_confirmed: bool = False


class Word(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    word: str = Field(max_length=500)
    probability: float | None = Field(default=None, ge=0, le=1)


class Caption(StrictModel):
    id: str = Field(max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=2000)
    words: list[Word] = Field(default_factory=list, max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    emoji: Literal['','💰','💡','🔥','😮','🎯','📈','❤️','🎉'] = ''
    emoji_enabled: bool = True
    timestamp_origin: Literal['recognized','estimated','user-estimated','user-corrected'] = 'recognized' 

    @model_validator(mode='after')
    def ordered(self):
        if self.end <= self.start:
            raise ValueError('Caption end must follow its start.')
        if any(w.end <= w.start or w.start < self.start - .15 or w.end > self.end + .15 for w in self.words):
            raise ValueError('Word timing must fit within its caption.')
        return self


class EditSpec(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    aspect_ratio: Literal['9:16', '1:1', '16:9'] = '9:16'
    layout: Literal['smart', 'center', 'manual', 'fit'] = 'smart'
    crop_x: float = Field(default=.5, ge=0, le=1)
    crop_y: float = Field(default=.5, ge=0, le=1)
    caption_preset: Literal['minimal', 'creator', 'karaoke', 'boxed', 'podcast','creator-bold','karaoke-pop','clean-editorial','podcast-dynamic','viral-energy'] = 'creator'
    font_family: Literal['DejaVu Sans', 'Liberation Sans'] = 'DejaVu Sans'
    font_size: int = Field(default=42, ge=18, le=100)
    text_color: str = '#FFFFFF'
    highlight_color: str = '#FFB45B'
    stroke: float = Field(default=2, ge=0, le=8)
    shadow: float = Field(default=1, ge=0, le=8)
    caption_x: float = Field(default=.5, ge=.1, le=.9)
    caption_y: float = Field(default=.78, ge=.1, le=.9)
    captions_enabled: bool = True
    captions: list[Caption] = Field(default_factory=list, max_length=3000)
    spec_version: int = Field(default=1, ge=1, le=3)
    output_resolution: Literal['720p','1080p'] = '720p'
    contextual_emojis: bool = True
    emoji_frequency: Literal['off','subtle','balanced','frequent'] = 'balanced'
    animated_captions: bool = True
    keyword_highlights: bool = True
    intensity: Literal['minimal','balanced','energetic'] = 'balanced'
    audio_normalize: bool = False
    opening_hook: bool = False
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def valid(self):
        if self.end - self.start < .3 or self.end - self.start > 180:
            raise ValueError('Clips must be between 0.3 and 180 seconds.')
        for value in (self.text_color, self.highlight_color):
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
                raise ValueError('Colors must be #RRGGBB.')
        return self
