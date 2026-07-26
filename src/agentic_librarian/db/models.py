from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WorkContributor(Base):
    __tablename__ = "work_contributors"

    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"), primary_key=True)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("authors.id"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String, default="Primary", primary_key=True)

    work: Mapped["Work"] = relationship(back_populates="contributors")
    author: Mapped["Author"] = relationship(back_populates="contributions")


# Junction table for Edition and Narrator
edition_narrators = Table(
    "edition_narrators",
    Base.metadata,
    Column("edition_id", ForeignKey("editions.id"), primary_key=True),
    Column("narrator_id", ForeignKey("narrators.id"), primary_key=True, index=True),
)


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    contributions: Mapped[list["WorkContributor"]] = relationship(back_populates="author")
    styles: Mapped[list["AuthorStyle"]] = relationship(back_populates="author", cascade="all, delete-orphan")

    # GH #95/schema-drift: model parity with the 48e3762d6c0c migration's raw-DDL
    # functional unique index (CREATE UNIQUE INDEX uq_authors_name_lower ON authors
    # (lower(name))) — must match that DDL exactly (same name) or autogenerate drifts.
    # Defined after `name` above so func.lower() closes over the real Column, not a string.
    __table_args__ = (Index("uq_authors_name_lower", func.lower(name), unique=True),)


class Work(Base):
    __tablename__ = "works"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    original_publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    genres: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    moods: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    # GH #97: stamped when a deep-enrichment pass completes (including confirmed-empty);
    # NULL means never deep-enriched. See etl/enrichment_sweep.py's requeue predicate.
    deep_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contributors: Mapped[list["WorkContributor"]] = relationship(back_populates="work", cascade="all, delete-orphan")
    editions: Mapped[list["Edition"]] = relationship(back_populates="work")
    tropes: Mapped[list["WorkTrope"]] = relationship(back_populates="work")
    suggestions: Mapped[list["Suggestions"]] = relationship(back_populates="work")
    styles: Mapped[list["WorkStyle"]] = relationship(back_populates="work", cascade="all, delete-orphan")


class Narrator(Base):
    __tablename__ = "narrators"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    styles: Mapped[list["NarratorStyle"]] = relationship(back_populates="narrator", cascade="all, delete-orphan")

    # GH #95/schema-drift: model parity with the 48e3762d6c0c migration's raw-DDL
    # functional unique index (same shape as Author.name above) — name must match exactly.
    __table_args__ = (Index("uq_narrators_name_lower", func.lower(name), unique=True),)


class Style(Base):
    """Standardized attributes for Authors (pacing, tone) or Narrators (voice diff, accent)."""

    __tablename__ = "styles"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)  # 'Author', 'Narrator', or 'Work'
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[Vector | None] = mapped_column(Vector(1536), nullable=True)

    author_links: Mapped[list["AuthorStyle"]] = relationship(back_populates="style")
    narrator_links: Mapped[list["NarratorStyle"]] = relationship(back_populates="style")
    work_links: Mapped[list["WorkStyle"]] = relationship(back_populates="style")


class AuthorStyle(Base):
    __tablename__ = "author_styles"

    author_id: Mapped[UUID] = mapped_column(ForeignKey("authors.id"), primary_key=True)
    style_id: Mapped[UUID] = mapped_column(ForeignKey("styles.id"), primary_key=True, index=True)
    attribute_type: Mapped[str] = mapped_column(String, primary_key=True)  # 'pacing', 'tone', 'style', 'humor', etc.

    author: Mapped["Author"] = relationship(back_populates="styles")
    style: Mapped["Style"] = relationship(back_populates="author_links")


class NarratorStyle(Base):
    __tablename__ = "narrator_styles"

    narrator_id: Mapped[UUID] = mapped_column(ForeignKey("narrators.id"), primary_key=True)
    style_id: Mapped[UUID] = mapped_column(ForeignKey("styles.id"), primary_key=True, index=True)
    attribute_type: Mapped[str] = mapped_column(String, primary_key=True)  # 'voice_differentiation', etc.

    narrator: Mapped["Narrator"] = relationship(back_populates="styles")
    style: Mapped["Style"] = relationship(back_populates="narrator_links")


class WorkStyle(Base):
    __tablename__ = "work_styles"

    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"), primary_key=True)
    style_id: Mapped[UUID] = mapped_column(ForeignKey("styles.id"), primary_key=True, index=True)
    attribute_type: Mapped[str] = mapped_column(String, primary_key=True)  # 'perspective', 'interiority', etc.

    work: Mapped["Work"] = relationship(back_populates="styles")
    style: Mapped["Style"] = relationship(back_populates="work_links")


class Trope(Base):
    __tablename__ = "tropes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[Vector | None] = mapped_column(Vector(1536), nullable=True)

    works: Mapped[list["WorkTrope"]] = relationship(back_populates="trope")


class WorkTrope(Base):
    __tablename__ = "work_tropes"

    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"), primary_key=True, nullable=False)
    trope_id: Mapped[UUID] = mapped_column(ForeignKey("tropes.id"), primary_key=True, nullable=False, index=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)

    work: Mapped["Work"] = relationship(back_populates="tropes")
    trope: Mapped["Trope"] = relationship(back_populates="works")


class Edition(Base):
    __tablename__ = "editions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    # GH #109/final-review Minor 5: NO index=True here (no standalone ix_editions_work_id) — the
    # leading column of uq_editions_work_format (work_id, format), just below, already serves
    # lookups on work_id alone; a separate single-column index would be redundant.
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"), nullable=False)
    isbn_13: Mapped[str | None] = mapped_column(String, nullable=True)
    format: Mapped[str | None] = mapped_column(String, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    work: Mapped["Work"] = relationship(back_populates="editions")
    narrators: Mapped[list["Narrator"]] = relationship(secondary=edition_narrators)
    reading_history: Mapped[list["ReadingHistory"]] = relationship(back_populates="edition")

    # GH #95/schema-drift: model parity with the 48e3762d6c0c migration's raw-DDL
    # UNIQUE (work_id, format) NULLS NOT DISTINCT index — name must match exactly.
    __table_args__ = (
        Index(
            "uq_editions_work_format",
            work_id,
            format,
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class ReadingHistory(Base):
    __tablename__ = "reading_history"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    edition_id: Mapped[UUID] = mapped_column(ForeignKey("editions.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    date_started: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Composite unique below (not on user_id above) since the constraint spans three columns.
    date_completed: Mapped[date] = mapped_column(Date, nullable=False)
    user_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    edition: Mapped["Edition"] = relationship(back_populates="reading_history")

    # GH #95/schema-drift: model parity with the 48e3762d6c0c migration's raw-DDL
    # UNIQUE (user_id, edition_id, date_completed) index — name must match exactly.
    __table_args__ = (
        Index(
            "uq_reading_history_user_edition_date",
            user_id,
            edition_id,
            date_completed,
            unique=True,
        ),
    )


class Suggestions(Base):
    __tablename__ = "suggestions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    work_id: Mapped[UUID] = mapped_column(ForeignKey("works.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    suggested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="Suggested", nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    work: Mapped["Work"] = relationship(back_populates="suggestions")

    # GH #95/schema-drift: model parity with the 48e3762d6c0c migration's raw-DDL
    # partial UNIQUE (user_id, work_id) WHERE status = 'Suggested' index — name and
    # predicate text must match exactly.
    __table_args__ = (
        Index(
            "uq_suggestions_active",
            user_id,
            work_id,
            unique=True,
            postgresql_where=text("status = 'Suggested'"),
        ),
    )


class Conversation(Base):
    """One chat thread (Lift 2). The active thread is the user's most-recent row;
    New chat inserts a new one. title is nullable now so the future switchable-list
    needs no migration. id doubles as the ADK session id so usage rows line up."""

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    """One turn in a conversation (Lift 2). role is 'user' or 'assistant'."""

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class User(Base):
    """An account holder (Lift 1, ADR-048). The catalog is communal; reading_history,
    suggestions, and usage are per-user. firebase_uid is NULL for invited users who
    have never signed in (claim-by-email links it on first verified sign-in)."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # lowercased; the invite key
    firebase_uid: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # #100 monetization: Ko-fi entitlement horizon (PR2 writes it; NULL/past = free tier).
    subscriber_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class Usage(Base):
    """One row per LLM call (Lift 1, ADR-048) — raw material for Lift 3 quotas/billing.
    key_source is 'app' until Lift 3's BYOK routing exists."""

    __tablename__ = "usage"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    key_source: Mapped[str] = mapped_column(String, default="app", nullable=False)  # 'app' | 'byok' (Lift 3)
    vendor: Mapped[str] = mapped_column(String, nullable=False)  # 'gemini' | 'anthropic'
    model: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class Payment(Base):
    """One row per Ko-fi webhook event (monetization arc 2/3) — the audit trail behind
    users.subscriber_until. Idempotency key = kofi_transaction_id (Ko-fi retries).
    matched_user_id NULL = payer email didn't match a user (CLI `payments match` fixes)."""

    __tablename__ = "payments"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    kofi_transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    kofi_type: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)  # lowercased at ingest
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    tier_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_subscription_payment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    matched_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    entitlement_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class UserCredential(Base):
    """BYOK placeholder (Lift 1 schema; the FEATURE lands in Lift 3). encrypted_key is
    KMS ciphertext ONLY — never plaintext, never logged (security.md). NO code reads or
    writes this table in Lift 1; it exists so BYOK needs no schema migration."""

    __tablename__ = "user_credentials"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    vendor: Mapped[str] = mapped_column(String, primary_key=True)
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kms_key_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class UserLibrary(Base):
    """A library system the user holds a card at (public OverDrive slug — NOT a secret,
    so this is plain prefs, not the UserCredential/keyring). Ordered by sort_order = the
    user's priority. provider is 'libby' in cut #1 (Hoopla has no availability signal)."""

    __tablename__ = "user_libraries"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String, primary_key=True, default="libby")
    library_slug: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class AvailabilityCache(Base):
    """Read-through cache for a (library, title, author) availability lookup. Keyed on
    NORMALIZED title+author so the recs consumer (has work_id) and the chat tool (has raw
    title/author) share rows. Freshness = now - fetched_at < TTL (default 4h)."""

    __tablename__ = "availability_cache"

    provider: Mapped[str] = mapped_column(String, primary_key=True)
    library_slug: Mapped[str] = mapped_column(String, primary_key=True)
    norm_title: Mapped[str] = mapped_column(String, primary_key=True)
    norm_author: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )  # caller always supplies the fetch time; no default by design


class DetectedDuplicate(Base):
    """GH #141: recorded when the deep pass's write-session dedup re-check resolves the
    invoked work's scout-canonical identity to a DIFFERENT existing work (a "redirect") —
    the persist legitimately lands on the twin (same book), so this table is the works-merge
    tool's queryable feed instead of a silent mis-stamp. work_id_a = the invoked/dirty work,
    work_id_b = the resolved twin. Composite PK so a repeat detection (e.g. a redelivered
    Cloud Tasks retry) upserts-or-ignores rather than piling up duplicate rows — see
    two_phase.enrich_deep's ON CONFLICT DO NOTHING insert."""

    __tablename__ = "detected_duplicates"

    work_id_a: Mapped[UUID] = mapped_column(ForeignKey("works.id"), primary_key=True, nullable=False)
    work_id_b: Mapped[UUID] = mapped_column(ForeignKey("works.id"), primary_key=True, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ImportJob(Base):
    """One bulk-import upload (Spec 2026-06-18). Progress is derived from import_rows,
    not stored here — so Cloud Tasks redelivery can never double-count."""

    __tablename__ = "import_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # 'goodreads' | 'generic'
    original_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ImportRow(Base):
    """One parsed source row. The Cloud Task targets this id; status is the idempotency
    boundary (a redelivered row whose status is already 'done' is a no-op)."""

    __tablename__ = "import_rows"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    import_job_id: Mapped[UUID] = mapped_column(ForeignKey("import_jobs.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    raw_title: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_author: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_format: Mapped[str | None] = mapped_column(String, nullable=True)  # normalized vocab
    raw_date: Mapped[str | None] = mapped_column(String, nullable=True)  # original text, for the report
    date_completed: Mapped[date | None] = mapped_column(Date, nullable=True)  # parsed; set for history rows
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    destination: Mapped[str] = mapped_column(String, nullable=False)  # 'history' | 'suggestion' | 'skip'
    shelf: Mapped[str | None] = mapped_column(String, nullable=True)  # drives the suggestion context tag
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending"
    )  # 'pending' | 'processing' | 'done' | 'failed' | 'skipped'
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)  # linked|created|duplicate|not_found
    skip_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    work_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )  # resolved Work; soft reference, no FK by design (import_rows are a transient staging/audit log)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
