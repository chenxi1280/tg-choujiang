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
from tg_lottery.application.participants import ParticipantService
from tg_lottery.application.queries import QueryService
from tg_lottery.application.settlement import SettlementService
from tg_lottery.application.winners import WinnerService
from tg_lottery.config import Settings
from tg_lottery.infrastructure.db.session import create_engine, create_session_factory
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.admin_flow import AdminFlow
from tg_lottery.telegram.campaign_creator import CampaignCreator
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.update_handler import LotteryUpdateHandler


@dataclass(frozen=True)
class Services:
    groups: GroupService
    campaigns: CampaignService
    participants: ParticipantService
    activity: ActivityService
    invitations: InvitationService
    settlement: SettlementService
    drawing: DrawingService
    winners: WinnerService
    tokens: CallbackTokenService
    sessions: AdminSessionService
    queries: QueryService


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
        invitations=InvitationService(uow_factory),
        settlement=SettlementService(uow_factory),
        drawing=DrawingService(uow_factory),
        winners=WinnerService(uow_factory),
        tokens=CallbackTokenService(uow_factory),
        sessions=AdminSessionService(uow_factory),
        queries=QueryService(session_factory),
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
    creator = CampaignCreator(
        bot=container.bot,
        bot_username=bot_username,
        sessions=services.sessions,
        tokens=services.tokens,
        campaigns=services.campaigns,
        queries=services.queries,
    )
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
    )
    return LotteryUpdateHandler(
        bot=container.bot,
        default_timezone=container.settings.timezone,
        admin_flow=admin_flow,
        member_flow=member_flow,
        tokens=services.tokens,
        groups=services.groups,
        activity=services.activity,
        invitations=services.invitations,
        queries=services.queries,
    )
