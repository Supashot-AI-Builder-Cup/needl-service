"""VerificationResult and everything it contains (the data ResultPage renders)."""

from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import Field, HttpUrl, StringConstraints, model_validator

from .common import ContractModel, LocalText, RiskLevel, SectionKey, UiSeverity


class SourceMetadata(ContractModel):
    kind: Literal["url", "screenshots"]
    url: HttpUrl | None = None
    canonical_url: HttpUrl | None = None
    marketplace: Annotated[str, StringConstraints(min_length=1, max_length=40)] | None = None
    fetched_at: datetime | None = None
    screenshot_count: int | None = Field(default=None, ge=2, le=10)

    @model_validator(mode="after")
    def validate_source(self):
        if self.kind == "url" and (self.url is None or self.screenshot_count is not None):
            raise ValueError("url source requires url and must not include screenshot_count")
        if self.kind == "screenshots" and (self.url is not None or self.screenshot_count is None):
            raise ValueError("screenshots source requires screenshot_count and no url")
        return self


class ListingMetadata(ContractModel):
    product_name: str | None = Field(default=None, max_length=500)
    store_name: str | None = Field(default=None, max_length=300)
    store_is_official: bool | None = None
    price: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    sold_count: int | None = Field(default=None, ge=0)


class ProductPreview(ContractModel):
    """Product fields known before analysis; ProductCard without score and verdict."""

    name: str
    price: str
    was: str | None
    rating: str
    ratings: str
    sold: LocalText
    store: str
    mall: bool
    loc: str
    # Main product photo; null when unknown (e.g. screenshot checks).
    image: str | None = None


class ProductCard(ProductPreview):
    score: int = Field(ge=0, le=100, description="UI risk score; higher means riskier")
    verdict: Literal["high", "safe"]


class Fact(ContractModel):
    label: LocalText
    value: LocalText


class TextSegment(ContractModel):
    text: str
    flagged: bool = False


class Term(ContractModel):
    term: str
    note: LocalText


class Claim(ContractModel):
    name: LocalText
    detail: LocalText
    status: UiSeverity
    label: LocalText


class Review(ContractModel):
    stars: int = Field(ge=1, le=5)
    who: str
    text: str


class ComplaintRow(ContractModel):
    category: LocalText
    count: int = Field(ge=0)


class NameCompareEvidence(ContractModel):
    kind: Literal["nameCompare"]
    caption: LocalText | None = None
    official: str
    store: str
    facts: list[Fact]


class DescriptionEvidence(ContractModel):
    kind: Literal["description"]
    caption: LocalText
    segments: list[TextSegment]
    terms: list[Term] | None = None


class ClaimsEvidence(ContractModel):
    kind: Literal["claims"]
    claims: list[Claim]


class HistogramEvidence(ContractModel):
    kind: Literal["histogram"]
    caption: LocalText
    values: list[int]
    hot: list[int]
    range: tuple[str, str]


class ReviewsEvidence(ContractModel):
    kind: Literal["reviews"]
    caption: LocalText
    reviews: list[Review]


class BottleVariant(ContractModel):
    dropper: str | None = None
    holo: bool | None = None
    thin: bool | None = None


class PhotoPairEvidence(ContractModel):
    kind: Literal["photoPair"]
    listingCaption: LocalText
    buyerCaption: LocalText
    buyerVariant: BottleVariant | None = None
    diffs: list[LocalText] | None = None
    facts: list[Fact] | None = None


class ComplaintQuote(ContractModel):
    text: str
    caption: LocalText


class ComplaintsEvidence(ContractModel):
    kind: Literal["complaints"]
    rows: list[ComplaintRow]
    quote: ComplaintQuote | None = None
    note: LocalText | None = None


Evidence = Annotated[
    Union[
        NameCompareEvidence,
        DescriptionEvidence,
        ClaimsEvidence,
        HistogramEvidence,
        ReviewsEvidence,
        PhotoPairEvidence,
        ComplaintsEvidence,
    ],
    Field(discriminator="kind"),
]


class Check(ContractModel):
    code: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    section: SectionKey
    severity: UiSeverity
    name: LocalText
    summary: LocalText
    evidence: list[Evidence]


class SuggestedStore(ContractModel):
    name: str
    badge: str
    price: str
    rating: str
    sold: LocalText
    why: LocalText
    link: HttpUrl
    logo: Literal["dermalune", "glowmart", "kulitku"]


class VerificationResult(ContractModel):
    id: UUID
    source: SourceMetadata
    metadata: ListingMetadata
    trust_score: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    displayName: str
    product: ProductCard
    checks: list[Check]
    stores: list[SuggestedStore]
    questions: list[LocalText]
    checked_at: datetime
    cached: bool

    @model_validator(mode="after")
    def validate_ui_scores(self):
        if self.product.score != 100 - self.trust_score:
            raise ValueError("product.score must equal 100 - trust_score")
        if (self.risk_level == RiskLevel.safe) != (self.product.verdict == "safe"):
            raise ValueError("product.verdict must be safe exactly when risk_level is safe")
        return self
