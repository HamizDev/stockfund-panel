
"""Routes inherit the panel's existing authentication. POST starts only prediction."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .candidates import CandidateCatalog
from .comparison_scores import summarize
from .comparisons import ComparisonManager, validate_matrix
from .data import enrich_chart_reference
from .evaluation import EvaluationManager
from .evaluation_data import actual_prices
from .service import MANAGER, BusyError

EVALUATOR = EvaluationManager(MANAGER, actual_prices)
COMPARISONS = ComparisonManager(MANAGER, EVALUATOR)
CANDIDATES = CandidateCatalog(COMPARISONS)




class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    symbol: str = Field(pattern=r'^\d{6}\.(SH|SZ|BJ)$')
    name: str = Field(default='', max_length=80)
    model: Literal['base'] = 'base'
    period: Literal['daily', '1m'] = 'daily'
    lookback: Literal[60, 120, 240, 400, 512] = 120
    horizon: int = Field(default=10, ge=1, le=20)
    samples: Literal[1, 3, 5] = 3

    @field_validator('lookback', 'horizon', 'samples', mode='before')
    @classmethod
    def integer_only(cls, value):
        if type(value) is not int:
            raise ValueError('预测参数必须为整数')
        return value

    @model_validator(mode='after')
    def horizon_by_period(self):
        if self.period == 'daily' and self.horizon not in (5, 10, 20):
            raise ValueError('日K预测步数仅支持5、10或20个交易日')
        return self


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    symbol: str = Field(pattern=r'^\d{6}\.(SH|SZ|BJ)$')
    name: str = Field(default='', max_length=80)
    period: Literal['daily', '1m'] = 'daily'
    models: list[Literal['base']] = Field(min_length=1, max_length=1)
    lookbacks: list[int] = Field(min_length=1, max_length=3)
    horizon: int = Field(default=10, ge=1, le=20)
    samples: Literal[1, 3, 5] = 3

    @field_validator('horizon', 'samples', mode='before')
    @classmethod
    def integer_only(cls, value):
        if type(value) is not int:
            raise ValueError('预测参数必须为整数')
        return value

    @model_validator(mode='after')
    def bounded_matrix(self):
        validate_matrix(self.model_dump())
        return self


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    comparison_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    trial_id: str = Field(pattern=r'^t\d{2}$')
    label: str = Field(min_length=1, max_length=60)




def research_call(function, *args):
    try:
        return function(*args)
    except BusyError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


def build_router():
    router = APIRouter(prefix='/api/custom/kronos', tags=['local-kronos'])









    @router.get('/status')
    def status():
        return MANAGER.status()









    @router.get('/comparisons')
    def comparisons(symbol: str | None = None, period: Literal['daily', '1m'] | None = None):
        return research_call(COMPARISONS.list, symbol, period)

    @router.get('/comparisons/summary')
    def comparison_summary(symbol: str | None = None, period: Literal['daily', '1m'] | None = None):
        return research_call(summarize, COMPARISONS, symbol, period)

    @router.post('/comparisons', status_code=202)
    def start_comparison(request: Request, body: ComparisonRequest):
        return research_call(COMPARISONS.start, body.model_dump(), request.app.state.repo,
                             getattr(request.app.state, 'capabilities', None))

    @router.get('/comparisons/{identity}')
    def comparison(identity: str):
        return research_call(COMPARISONS.get, identity)

    @router.post('/comparisons/{identity}/cancel')
    def cancel_comparison(identity: str):
        return research_call(COMPARISONS.cancel, identity)

    @router.get('/candidates')
    def candidates():
        return research_call(CANDIDATES.list)

    @router.post('/candidates', status_code=201)
    def save_candidate(body: CandidateRequest):
        return research_call(CANDIDATES.save, body.comparison_id, body.trial_id, body.label)

    @router.post('/candidates/{identity}/retire')
    def retire_candidate(identity: str):
        return research_call(CANDIDATES.retire, identity)


    @router.post('/jobs', status_code=202)
    def start(request: Request, body: PredictionRequest):
        try:
            return MANAGER.start(body.model_dump(), request.app.state.repo,
                                 getattr(request.app.state, 'capabilities', None))
        except BusyError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get('/jobs/{identity}')
    def job(request: Request, identity: str):
        try:
            return enrich_chart_reference(MANAGER.get(identity), request.app.state.repo)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.get('/jobs/{identity}/evaluation')
    def evaluation(identity: str):
        try:
            return EVALUATOR.get(identity)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.post('/jobs/{identity}/cancel')
    def cancel(identity: str):
        try:
            return MANAGER.cancel(identity)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    return router
