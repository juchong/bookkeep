from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from datetime import datetime
from typing import Literal, Optional, List

# User schemas
class UserBase(BaseModel):
    email: EmailStr
    username: str
    full_name: Optional[str] = None

class UserCreate(UserBase):
    password: str
    is_admin: Optional[bool] = False
    can_request_ebook: Optional[bool] = True
    can_request_audiobook: Optional[bool] = True
    can_download: Optional[bool] = True
    auto_approve_ebooks: Optional[bool] = True
    auto_approve_audiobooks: Optional[bool] = True

class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    has_password: bool = True
    can_request_ebook: Optional[bool] = True
    can_request_audiobook: Optional[bool] = True
    can_download: Optional[bool] = True
    auto_approve_ebooks: Optional[bool] = True
    auto_approve_audiobooks: Optional[bool] = True
    created_at: datetime
    updated_at: Optional[datetime] = None

    @model_validator(mode='before')
    @classmethod
    def handle_none_values(cls, data):
        """Convert None values to defaults for boolean fields"""
        if isinstance(data, dict):
            if data.get('can_request_ebook') is None:
                data['can_request_ebook'] = True
            if data.get('can_request_audiobook') is None:
                data['can_request_audiobook'] = True
            if data.get('can_download') is None:
                data['can_download'] = True
            if data.get('auto_approve_ebooks') is None:
                data['auto_approve_ebooks'] = True
            if data.get('auto_approve_audiobooks') is None:
                data['auto_approve_audiobooks'] = True
            data['has_password'] = bool(data.get('hashed_password'))
        else:
            data.has_password = bool(getattr(data, 'hashed_password', None))
        return data

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    can_request_ebook: Optional[bool] = None
    can_request_audiobook: Optional[bool] = None
    can_download: Optional[bool] = None
    auto_approve_ebooks: Optional[bool] = None
    auto_approve_audiobooks: Optional[bool] = None

class UserWithRequestsResponse(UserResponse):
    total_requests: Optional[int] = None

# Book schemas
class BookBase(BaseModel):
    title: str
    author: str
    isbn: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = None
    genre: Optional[str] = None
    published_date: Optional[str] = None
    rating: Optional[float] = None
    page_count: Optional[int] = None
    hardcover_id: Optional[int] = None
    hardcover_slug: Optional[str] = None
    default_edition_id: Optional[int] = None
    default_physical_edition_id: Optional[int] = None
    default_ebook_edition_id: Optional[int] = None
    default_audio_edition_id: Optional[int] = None
    series: Optional[str] = None
    series_id: Optional[int] = None
    series_position: Optional[float] = None
    genres: Optional[List[str]] = None
    ebook_available: Optional[bool] = False
    audiobook_available: Optional[bool] = False

    @field_validator("published_date", mode="before")
    @classmethod
    def normalize_published_date(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return None if text.lower() in {"", "none", "null", "nan", "undefined"} else text

class BookCreate(BookBase):
    pass

class BookResponse(BookBase):
    id: int
    ebook_available: bool = False
    audiobook_available: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None

    @property
    def is_available(self) -> bool:
        """Book is available if either format is available."""
        return self.ebook_available or self.audiobook_available

    class Config:
        from_attributes = True

# BookRequest schemas
class BookRequestBase(BaseModel):
    book_id: int
    format: str
    notes: Optional[str] = None
    edition_id: Optional[int] = None

class BookRequestCreate(BookRequestBase):
    pass

class BookRequestUpdate(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None

class BookRequestResponse(BaseModel):
    id: int
    book_id: int
    user_id: int
    format: str
    status: str
    source: Optional[str] = "user_request"  # 'user_request' or 'booklore_import'
    notes: Optional[str] = None
    admin_notes: Optional[str] = None
    # Deprecated: Readarr fields kept for backward compatibility
    readarr_book_id: Optional[int] = None
    readarr_received: Optional[bool] = None
    readarr_search_triggered: Optional[bool] = None
    readarr_search_status_code: Optional[int] = None
    readarr_message: Optional[str] = None
    edition_id: Optional[int] = None
    auto_search_attempts: int = 0
    last_search_at: Optional[datetime] = None
    next_search_at: Optional[datetime] = None
    last_search_error: Optional[str] = None
    download_task_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    book: Optional[BookResponse] = None
    user: Optional[UserResponse] = None

    class Config:
        from_attributes = True


# Media issue reporting schemas
MediaIssueType = Literal[
    "wrong_language",
    "incomplete",
    "wrong_content",
    "file_structure",
    "unplayable",
    "quality",
    "metadata",
    "other",
]
MediaFormat = Literal["ebook", "audiobook"]


class MediaIssueCreate(BaseModel):
    book_id: Optional[int] = None
    format: Optional[MediaFormat] = None
    issue_type: MediaIssueType
    report_text: str = Field(min_length=5, max_length=4000)
    request_id: Optional[int] = None
    download_task_id: Optional[int] = None
    page_path: Optional[str] = Field(default=None, max_length=500)
    is_critical: bool = False
    critical_explanation: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_media_context(self):
        if self.issue_type != "other" and (self.book_id is None or self.format is None):
            raise ValueError("A book and format are required for this problem type")
        if (self.book_id is None) != (self.format is None):
            raise ValueError("Book and format must be provided together")
        if self.is_critical and not (self.critical_explanation or "").strip():
            raise ValueError("Explain the inappropriate or violent content")
        return self


class MediaIssueReportUpdate(BaseModel):
    report_text: str = Field(min_length=5, max_length=4000)
    is_critical: bool = False
    critical_explanation: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_critical_explanation(self):
        if self.is_critical and not (self.critical_explanation or "").strip():
            raise ValueError("Explain the inappropriate or violent content")
        return self


class MediaIssueStillBroken(BaseModel):
    explanation: str = Field(min_length=5, max_length=2000)


class MediaIssueDone(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)


class MediaIssueAdminUpdate(BaseModel):
    issue_type: Optional[MediaIssueType] = None
    is_critical: Optional[bool] = None


class AutoDownloadSettingsUpdate(BaseModel):
    enabled: bool = False
    dry_run: bool = True
    process_existing_backlog: bool = True
    interval_seconds: int = Field(default=900, ge=300, le=604800)
    batch_size: int = Field(default=5, ge=1, le=50)
    max_active_downloads: int = Field(default=10, ge=1, le=100)
    minimum_score: float = Field(default=70.0, ge=0, le=100)
    ebook_formats: List[str] = Field(default_factory=lambda: ["epub", "azw3", "mobi", "pdf"])
    audiobook_formats: List[str] = Field(default_factory=lambda: ["m4b", "mp3", "flac"])
    preferred_languages: List[str] = Field(default_factory=lambda: ["en"])
    protocol_order: List[str] = Field(default_factory=lambda: ["usenet", "torrent"])
    minimum_seeders: int = Field(default=1, ge=0, le=10000)
    ebook_min_size_mb: float = Field(default=0.1, ge=0)
    ebook_max_size_mb: float = Field(default=500.0, gt=0)
    audiobook_min_size_mb: float = Field(default=10.0, ge=0)
    audiobook_max_size_mb: float = Field(default=5000.0, gt=0)
    retry_schedule_seconds: List[int] = Field(
        default_factory=lambda: [900, 3600, 21600, 86400],
        min_length=1,
        max_length=10,
    )
    categoryless_fallback: bool = True

    @field_validator("ebook_formats", "audiobook_formats", "preferred_languages", "protocol_order")
    @classmethod
    def normalize_string_lists(cls, value: List[str]) -> List[str]:
        normalized = []
        for item in value:
            item = item.strip().lower()
            if item and item not in normalized:
                normalized.append(item)
        if not normalized:
            raise ValueError("At least one value is required")
        return normalized

    @field_validator("protocol_order")
    @classmethod
    def validate_protocols(cls, value: List[str]) -> List[str]:
        unsupported = set(value) - {"torrent", "usenet"}
        if unsupported:
            raise ValueError(f"Unsupported protocols: {', '.join(sorted(unsupported))}")
        return value

    @field_validator("retry_schedule_seconds")
    @classmethod
    def validate_retry_schedule(cls, value: List[int]) -> List[int]:
        if any(delay < 60 or delay > 604800 for delay in value):
            raise ValueError("Retry delays must be between 60 seconds and 7 days")
        if any(delay < value[index - 1] for index, delay in enumerate(value) if index > 0):
            raise ValueError("Retry delays must stay the same or increase over time")
        return value

    @model_validator(mode="after")
    def validate_size_ranges(self):
        if self.ebook_min_size_mb >= self.ebook_max_size_mb:
            raise ValueError("Ebook minimum size must be lower than maximum size")
        if self.audiobook_min_size_mb >= self.audiobook_max_size_mb:
            raise ValueError("Audiobook minimum size must be lower than maximum size")
        return self


class AutoDownloadSettingsResponse(AutoDownloadSettingsUpdate):
    last_run_started_at: Optional[datetime] = None
    last_run_completed_at: Optional[datetime] = None
    last_run_summary: Optional[dict] = None


class AutoDownloadRunRequest(BaseModel):
    dry_run: bool = True

# Hardcover API schemas
class HardcoverAuthor(BaseModel):
    id: int
    name: str
    slug: Optional[str] = None
    bio: Optional[str] = None
    image_url: Optional[str] = None

class HardcoverCachedImage(BaseModel):
    """Model for cached_image field which comes as a dict"""
    id: Optional[int] = None
    url: Optional[str] = None
    color_name: Optional[str] = None
    
    class Config:
        extra = "allow"  # Allow extra fields we don't know about

class HardcoverCachedContributor(BaseModel):
    """Model for cached_contributors list items"""
    author: Optional[dict] = None  # Can be complex, keeping as dict for now
    contribution: Optional[str] = None

class HardcoverContribution(BaseModel):
    contribution: Optional[str] = None
    author: HardcoverAuthor

class HardcoverSeries(BaseModel):
    id: int
    name: str
    primary_books_count: Optional[int] = None

class HardcoverAuthorSeries(BaseModel):
    id: int
    name: str
    books_count: Optional[int] = None

class HardcoverBookSeries(BaseModel):
    series_id: Optional[int] = None
    series: Optional["HardcoverSeries"] = None
    position: Optional[float] = None
    book: Optional["HardcoverBook"] = None  # Used in series detail queries

class HardcoverTag(BaseModel):
    tag: str

class HardcoverTagging(BaseModel):
    tag: HardcoverTag

class HardcoverBook(BaseModel):
    id: int
    title: str
    slug: Optional[str] = None
    release_year: Optional[int] = None
    release_date: Optional[str] = None
    pages: Optional[int] = None
    description: Optional[str] = None
    cached_image: Optional[HardcoverCachedImage] = None
    cached_contributors: Optional[List[HardcoverCachedContributor]] = None
    rating: Optional[float] = None
    ratings_count: Optional[int] = None
    users_count: Optional[int] = None
    activities_count: Optional[int] = None
    compilation: Optional[bool] = None
    default_edition_id: Optional[int] = None
    default_physical_edition_id: Optional[int] = None
    default_ebook_edition_id: Optional[int] = None
    default_audio_edition_id: Optional[int] = None
    book_series: Optional[List[HardcoverBookSeries]] = None
    contributions: Optional[List[HardcoverContribution]] = None
    taggings: Optional[List[HardcoverTagging]] = None
    ebook_available: Optional[bool] = False  # Ebook is available in library
    audiobook_available: Optional[bool] = False  # Audiobook is available in library

class HardcoverAuthorResponse(BaseModel):
    author: Optional[HardcoverAuthor] = None
    books: List[HardcoverBook] = []
    books_total: int = 0
    series: List[HardcoverAuthorSeries] = []
    series_total: int = 0

class HardcoverEdition(BaseModel):
    id: int
    title: Optional[str] = None
    edition_format: Optional[str] = None
    physical_format: Optional[str] = None
    reading_format_id: Optional[int] = None
    pages: Optional[int] = None
    isbn_10: Optional[str] = None
    isbn_13: Optional[str] = None
    asin: Optional[str] = None

class HardcoverBookDetail(HardcoverBook):
    """Extended book model with additional fields for detail view"""
    editions: Optional[List[HardcoverEdition]] = None

class HardcoverTrendingResponse(BaseModel):
    ids: List[int]
    error: Optional[str] = None

class HardcoverBooksResponse(BaseModel):
    books: List[HardcoverBook]

class HardcoverBookDetailResponse(BaseModel):
    books_by_pk: Optional[HardcoverBookDetail] = None

class HardcoverEditionPickerItem(BaseModel):
    id: int
    title: Optional[str] = None
    score: Optional[float] = None
    reading_format_id: Optional[int] = None
    reading_format: Optional[str] = None
    language: Optional[str] = None
    language_code2: Optional[str] = None
    publisher: Optional[str] = None
    pages: Optional[int] = None
    audio_seconds: Optional[int] = None
    edition_format: Optional[str] = None
    release_date: Optional[str] = None
    release_year: Optional[int] = None

class HardcoverEditionPickerResponse(BaseModel):
    default_cover_edition_id: Optional[int] = None
    default_ebook_edition_id: Optional[int] = None
    default_audio_edition_id: Optional[int] = None
    editions: List[HardcoverEditionPickerItem]

class HardcoverPromptBook(BaseModel):
    book: Optional[HardcoverBook] = None

class HardcoverPrompt(BaseModel):
    slug: Optional[str] = None
    answers_count: Optional[int] = None
    books_count: Optional[int] = None
    question: Optional[str] = None
    prompt_books: Optional[List[HardcoverPromptBook]] = None

class HardcoverPromptSummary(BaseModel):
    answers_count: Optional[int] = None
    prompt: Optional[HardcoverPrompt] = None

class HardcoverBookPromptsResponse(BaseModel):
    prompt_summaries: Optional[List[HardcoverPromptSummary]] = None

class HardcoverSeriesDetail(BaseModel):
    id: int
    name: str
    author: Optional[dict] = None
    books_count: Optional[int] = None
    book_series: Optional[List[HardcoverBookSeries]] = None

class HardcoverSeriesResponse(BaseModel):
    series_by_pk: Optional[HardcoverSeriesDetail] = None

class SearchAuthorResult(BaseModel):
    name: str
    books_count: int

class SearchSeriesResult(BaseModel):
    id: int
    name: str
    books_count: Optional[int] = None

class SearchGroupedResponse(BaseModel):
    series: List[SearchSeriesResult] = []
    authors: List[SearchAuthorResult] = []
    books: List[HardcoverBook] = []

class HardcoverPopularSeriesItem(BaseModel):
    id: int
    name: str
    books_count: int
    owned_count: Optional[int] = 0
    first_book: Optional[HardcoverBook] = None

class HardcoverPopularSeriesResponse(BaseModel):
    series: List[HardcoverPopularSeriesItem]
    total: int = 0
    has_more: bool = False
    offset: int = 0
    limit: int = 0

class HardcoverSearchResult(BaseModel):
    """Individual search result item"""
    id: int
    title: str
    slug: Optional[str] = None
    release_year: Optional[int] = None
    release_date: Optional[str] = None
    pages: Optional[int] = None
    description: Optional[str] = None
    cached_image: Optional[HardcoverCachedImage] = None
    cached_contributors: Optional[List[HardcoverCachedContributor]] = None
    rating: Optional[float] = None
    ratings_count: Optional[int] = None
    users_count: Optional[int] = None
    activities_count: Optional[int] = None
    book_series: Optional[List[HardcoverBookSeries]] = None
    contributions: Optional[List[HardcoverContribution]] = None
    taggings: Optional[List[HardcoverTagging]] = None

class HardcoverSearchResponse(BaseModel):
    """Response from Hardcover search query"""
    error: Optional[str] = None
    page: Optional[int] = None
    per_page: Optional[int] = None
    query: Optional[str] = None
    query_type: Optional[str] = None
    results: Optional[List[HardcoverSearchResult]] = None

# Settings schemas
class SettingsResponse(BaseModel):
    hardcover_api_token: Optional[str] = None
    hardcover_api_token_source: str  # 'env' or 'ui'
    has_hardcover_token: bool

class SettingsUpdate(BaseModel):
    hardcover_api_token: Optional[str] = None

# Readarr Server schemas
class ReadarrServerBase(BaseModel):
    name: str
    hostname: str
    port: int = 8787
    use_ssl: bool = False
    api_key: str
    url_base: Optional[str] = None
    is_default: bool = False
    is_audiobook: bool = False
    ebook_quality_profile_id: Optional[int] = None
    ebook_root_folder: Optional[str] = None
    ebook_tags: Optional[str] = None
    audiobook_quality_profile_id: Optional[int] = None
    audiobook_root_folder: Optional[str] = None
    audiobook_tags: Optional[str] = None

class ReadarrServerCreate(ReadarrServerBase):
    pass

class ReadarrServerUpdate(BaseModel):
    name: Optional[str] = None
    hostname: Optional[str] = None
    port: Optional[int] = None
    use_ssl: Optional[bool] = None
    api_key: Optional[str] = None
    url_base: Optional[str] = None
    is_default: Optional[bool] = None
    is_audiobook: Optional[bool] = None
    ebook_quality_profile_id: Optional[int] = None
    ebook_root_folder: Optional[str] = None
    ebook_tags: Optional[str] = None
    audiobook_quality_profile_id: Optional[int] = None
    audiobook_root_folder: Optional[str] = None
    audiobook_tags: Optional[str] = None

class ReadarrServerResponse(ReadarrServerBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class ReadarrTestConnectionRequest(BaseModel):
    hostname: str
    port: int = 8787
    use_ssl: bool = False
    api_key: str
    url_base: Optional[str] = None

class ReadarrQualityProfile(BaseModel):
    id: int
    name: str

class ReadarrRootFolder(BaseModel):
    path: str
    freeSpace: Optional[int] = None
    totalSpace: Optional[int] = None

class ReadarrTag(BaseModel):
    id: int
    label: str

class ReadarrTestConnectionResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    quality_profiles: Optional[List[ReadarrQualityProfile]] = None
    root_folders: Optional[List[ReadarrRootFolder]] = None
    tags: Optional[List[ReadarrTag]] = None

class ReadarrAvailabilityBatchRequest(BaseModel):
    hardcover_ids: List[int]
    isbn_map: Optional[dict[int, List[str]]] = None

class ReadarrAvailabilityItem(BaseModel):
    hardcover_id: int
    ebook: bool
    audiobook: bool

class ReadarrAvailabilityBatchResponse(BaseModel):
    results: List[ReadarrAvailabilityItem]


# Booklore Server schemas
class BookloreServerBase(BaseModel):
    name: str
    url: str  # Full URL like https://booklore.example.com
    username: str
    is_default: bool = False
    ebook_library_id: Optional[int] = None
    audiobook_library_id: Optional[int] = None

class BookloreServerCreate(BookloreServerBase):
    password: str

class BookloreServerUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    is_default: Optional[bool] = None
    ebook_library_id: Optional[int] = None
    audiobook_library_id: Optional[int] = None

class BookloreServerResponse(BookloreServerBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class BookloreTestConnectionRequest(BaseModel):
    url: str
    username: str
    password: str

class BookloreTestConnectionResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    libraries: Optional[List[dict]] = None  # List of Booklore libraries

# Audiobookshelf Server schemas
class AudiobookshelfServerBase(BaseModel):
    name: str
    url: str  # Full URL like https://abs.example.com
    is_default: bool = False
    library_id: Optional[str] = None  # ABS library UUID; null = scan all libraries

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        if not v.startswith(('http://', 'https://')):
            raise ValueError('URL must start with http:// or https://')
        return v.rstrip('/')

class AudiobookshelfServerCreate(AudiobookshelfServerBase):
    api_key: str

class AudiobookshelfServerUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    api_key: Optional[str] = None
    is_default: Optional[bool] = None
    library_id: Optional[str] = None

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        if v is not None and not v.startswith(('http://', 'https://')):
            raise ValueError('URL must start with http:// or https://')
        return v.rstrip('/') if v else v

class AudiobookshelfServerResponse(AudiobookshelfServerBase):
    """Response schema — intentionally excludes api_key for security"""
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class AudiobookshelfTestConnectionRequest(BaseModel):
    url: str
    api_key: str

class AudiobookshelfTestConnectionResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    libraries: Optional[List[dict]] = None  # List of Audiobookshelf libraries


class BookloreBook(BaseModel):
    """Book from Booklore API"""
    id: int
    title: Optional[str] = None
    libraryId: Optional[int] = None
    libraryName: Optional[str] = None
    filePath: Optional[str] = None
    hardcover_id: Optional[str] = None  # From metadata.hardcoverId
    
    class Config:
        extra = "allow"
