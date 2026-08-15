from dataclasses import dataclass
from functools import partial

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tg_lottery.application.activity import ActivityService
from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackTokenService
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.groups import GroupService
from tg_lottery.application.invitations import InvitationService
from tg_lottery.application.message_stats import MessageStatsService
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.application.winners import WinnerService
from tg_lottery.config import Settings
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.admin_flow import AdminFlow
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.creator_renderer import CreatorRenderer
from tg_lottery.telegram.creator_settings import CreatorSettings
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.personal_flow import PersonalFlow
from tg_lottery.telegram.update_handler import LotteryUpdateHandler


@dataclass(frozen=True)
class Services:
    groups: GroupService
    campaigns: CampaignService
    participants: ParticipantService
    activity: ActivityService
    message_stats: MessageStatsService
    invitations: InvitationService
    settlement: SettlementService
    drawing: DrawingService
    winners: WinnerService
    tokens: CallbackTokenService
    sessions: AdminSessionService
    queries: QueryService
    profiles: UserProfileService


@dataclass(frozen=True)
class Container:
    settings: Settings
    bot: Bot
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    services: Services


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    uow_factory = partial(UnitOfWork, session_factory)
    services = Services(
        groups=GroupService(uow_factory),
        campaigns=CampaignService(uow_factory),
        participants=ParticipantService(uow_factory),
        activity=ActivityService(uow_factory),
        message_stats=MessageStatsService(uow_factory),
        invitations=InvitationService(uow_factory),
        settlement=SettlementService(uow_factory),
        drawing=DrawingService(uow_factory),
        winners=WinnerService(uow_factory),
        tokens=CallbackTokenService(uow_factory),
        sessions=AdminSessionService(uow_factory),
        queries=QueryService(session_factory),
        profiles=UserProfileService(uow_factory),
    )
    return Container(
        settings=settings,
        bot=Bot(settings.bot_token),
        engine=engine,
        session_factory=session_factory,
        services=services,
    )


def build_update_handler(container: Container, bot_username: str) -> LotteryUpdateHandler:
    services = container.services
    creator = _build_creator(container, bot_username)
    admin_flow = AdminFlow(
        bot=container.bot,
        creator=creator,
        tokens=services.tokens,
        campaigns=services.campaigns,
        drawing=services.drawing,
        winners=services.winners,
        queries=services.queries,
    )
    member_flow = MemberFlow(
        bot=container.bot,
        participants=services.participants,
        queries=services.queries,
        cipher=SecretCipher(container.settings.app_encryption_key),
    )
    personal_flow = PersonalFlow(
        profiles=services.profiles,
        queries=services.queries,
        bot_username=bot_username,
    )
    return LotteryUpdateHandler(
        bot=container.bot,
        default_timezone=container.settings.timezone,
        admin_flow=admin_flow,
        member_flow=member_flow,
        personal_flow=personal_flow,
        tokens=services.tokens,
        groups=services.groups,
        activity=services.activity,
        invitations=services.invitations,
        queries=services.queries,
        stats=services.message_stats,
        bot_username=bot_username,
    )


def _build_creator(container: Container, bot_username: str) -> CampaignCreator:
    services = container.services
    renderer = CreatorRenderer(
        bot=container.bot,
        tokens=services.tokens,
        queries=services.queries,
        sessions=services.sessions,
    )
    creator_settings = CreatorSettings(
        sessions=services.sessions,
        renderer=renderer,
        profiles=services.profiles,
        queries=services.queries,
        bot=container.bot,
    )
    return CampaignCreator(
        bot=container.bot,
        bot_username=bot_username,
        sessions=services.sessions,
        campaigns=services.campaigns,
        queries=services.queries,
        renderer=renderer,
        settings=creator_settings,
        profiles=services.profiles,
    )
