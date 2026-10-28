"""Keep outbound Telegram API calls to one HTTP request with no redirects."""

from importlib.metadata import PackageNotFoundError, version

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiohttp import ClientConnectorError, ClientError


class PreRequestConnectionError(TelegramNetworkError):
    """The single, non-redirecting HTTP request could not establish a connection."""


class SingleRequestSession(AiohttpSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._connection_proof_supported = _verified_versions() and self._proxy is None

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
        session = await self.create_session()
        url = self.api.api_url(token=bot.token, method=method.__api_method__)
        form = self.build_form_data(bot=bot, method=method)
        try:
            async with session.post(
                url,
                data=form,
                timeout=self.timeout if timeout is None else timeout,
                allow_redirects=False,
            ) as response:
                raw_result = await response.text()
        except TimeoutError as exc:
            raise TelegramNetworkError(method=method, message="Request timeout error") from exc
        except ClientConnectorError as exc:
            error_type = (
                PreRequestConnectionError
                if self._connection_proof_supported and self._proxy is None
                else TelegramNetworkError
            )
            raise error_type(method=method, message=f"{type(exc).__name__}: {exc}") from exc
        except ClientError as exc:
            raise TelegramNetworkError(
                method=method, message=f"{type(exc).__name__}: {exc}"
            ) from exc
        if 300 <= response.status < 400 or response.status >= 500:
            raise TelegramNetworkError(
                method=method, message=f"Unconfirmed HTTP response: {response.status}"
            )
        result = self.check_response(
            bot=bot, method=method, status_code=response.status, content=raw_result
        )
        return result.result


def _verified_versions() -> bool:
    try:
        return (version("aiogram"), version("aiohttp")) == ("3.31.0", "3.14.5")
    except PackageNotFoundError:
        return False
