"""Model and bridge setup shared by real and simulated carriers."""

import json

from agent_service.calls.bridge import NativeAudioBridge
from agent_service.calls.live import LiveAudioSession
from agent_service.calls.live_bridge import LiveBridge
from agent_service.calls.realtime import AzureAudioSession


class ModelAudioGateway:
    def audio(self, spec):
        if self.config.audio_mode == "live":
            return LiveAudioSession(
                self.config.realtime_base_url,
                self.config.realtime_api_key,
                self.config.live_model,
                voice=self.config.live_voice,
                task=json.dumps(spec.model_dump(exclude={"destination"}), ensure_ascii=False),
            )
        return AzureAudioSession(
            self.config.realtime_base_url,
            self.config.realtime_api_key,
            self.config.realtime_model,
            task=json.dumps(spec.model_dump(exclude={"destination"}), ensure_ascii=False),
        )

    def bridge(self, model, spec):
        if self.config.audio_mode == "live":
            return LiveBridge(
                model, listen_first=spec.listen_first, opening_message=spec.opening_message
            )
        return NativeAudioBridge(
            model, listen_first=spec.listen_first, opening_message=spec.opening_message
        )
