"""Ports: the small interfaces the pipeline depends on. Concrete adapters (BM25 index, rule-based planner, section engine,
Ollama client, or a remote HTTP client for any of them) only have to satisfy these shapes."""
from typing import Any, Dict, Iterable, Iterator, List, Optional, Protocol, Tuple


class Retriever(Protocol):
    n: int
    chunks: List[Dict[str, Any]]

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]: ...
    def correct(self, query: str) -> Tuple[str, List[Tuple[str, str]]]: ...
    def expand(self, query: str) -> str: ...
    def unseen_words(self, query: str) -> List[str]: ...
    def entity_query(self, query: str) -> bool: ...
    def idf(self, term: str) -> float: ...


class Planner(Protocol):
    def analyze(self, question: str) -> Dict[str, Any]:
        """intent, format, length, entity, parts, vague ..."""


class AnswerEngine(Protocol):
    def answer(self, plan: Dict[str, Any], tindex: Any = None) -> Optional[Any]: ...
    def topic(self, plan: Dict[str, Any]) -> Optional[Any]: ...
    def clarify(self, plan: Dict[str, Any]) -> List[Dict[str, str]]: ...
    def related(self, plan: Dict[str, Any], k: int = 3) -> List[str]: ...


class LanguageModel(Protocol):
    def stream(self, model: str, messages: List[Dict[str, str]], options: Dict[str, Any], timeout: float) -> Iterator[Tuple[str, Any]]:
        """Yields ("token", text) pieces, then ("stats", dict)."""


class Step(Protocol):
    """One stage of the pipeline. Return None to pass the request on, or an iterable of events to end it."""
    def run(self, ctx: "Any") -> Optional[Iterable[Dict[str, Any]]]: ...
