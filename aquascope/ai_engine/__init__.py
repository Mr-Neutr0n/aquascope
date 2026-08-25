"""AI-powered research methodology recommendation engine."""

from aquascope.ai_engine.agent import AgentResult, HydroAgent
from aquascope.ai_engine.knowledge_base import (
    METHODOLOGIES,
    ResearchMethodology,
    get_methodology,
    search_methodologies,
)
from aquascope.ai_engine.model_recommender import ModelRecommendation, ModelRecommender
from aquascope.ai_engine.planner import ChallengePlanner, ChallengeSpec
from aquascope.ai_engine.recommender import (
    DatasetProfile,
    Recommendation,
    RecommendationResult,
    recommend,
    recommend_with_llm,
    recommend_with_llm_detailed,
)

__all__ = [
    "METHODOLOGIES",
    "ResearchMethodology",
    "get_methodology",
    "search_methodologies",
    "DatasetProfile",
    "Recommendation",
    "RecommendationResult",
    "recommend",
    "recommend_with_llm",
    "recommend_with_llm_detailed",
    "ChallengePlanner",
    "ChallengeSpec",
    "ModelRecommender",
    "ModelRecommendation",
    "HydroAgent",
    "AgentResult",
]
