from dify_plugin import ModelProvider


class VolcengineASRProvider(ModelProvider):
    def validate_provider_credentials(self, credentials: dict) -> None:
        # Credentials are validated per customizable speech-to-text model.
        pass
