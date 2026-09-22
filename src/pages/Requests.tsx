import { useState } from 'react';
import { Clock, CheckCircle, XCircle, Loader2, Trash2, CheckCircle2, Library, Inbox, Search, RotateCw } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn, formatPublicationYear } from '@/lib/utils';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { requestsApi } from '@/lib/api';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';
import { useUser } from '@/contexts/UserContext';
import type { BookRequest } from '@/types/book';
import { AutomationControls } from '@/components/requests/AutomationControls';

const PAGE_SIZE = 50;

interface RawRequestBook {
  id: number;
  title?: string;
  author?: string;
  cover_url?: string;
  description?: string;
  published_date?: string;
  genres?: string | string[];
  rating?: number;
  series?: string;
  series_position?: number;
  hardcover_id?: number;
  hardcover_slug?: string;
  isbn?: string;
  page_count?: number;
}

interface RawBookRequest {
  id: number;
  book_id: number;
  book?: RawRequestBook;
  user_id: number;
  user?: { username?: string; full_name?: string };
  format: 'ebook' | 'audiobook';
  status: BookRequest['status'];
  source?: 'user_request' | 'booklore_import';
  notes?: string;
  admin_notes?: string;
  auto_search_attempts?: number;
  last_search_at?: string;
  next_search_at?: string;
  last_search_error?: string;
  download_task_id?: number;
  created_at: string;
  updated_at: string;
}

const statusConfig = {
  requested: { label: 'Requested', className: 'status-requested', icon: Clock },
  approved: { label: 'Approved', className: 'status-approved', icon: CheckCircle },
  processing: { label: 'Processing', className: 'status-processing', icon: Loader2 },
  available: { label: 'Available', className: 'status-available', icon: CheckCircle },
  denied: { label: 'Denied', className: 'status-denied', icon: XCircle },
  not_found: { label: 'Not Found', className: 'status-not-found', icon: XCircle },
  pending: { label: 'Pending', className: 'status-requested', icon: Clock },
};

function formatRelativeTime(dateString: string | null | undefined): string {
  if (!dateString) return 'recently';

  const date = new Date(dateString);
  if (isNaN(date.getTime())) return 'recently';

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays < 0 || diffDays > 3650) return 'recently';

  if (diffDays === 0) return 'today';
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
  if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;
  return `${Math.floor(diffDays / 365)} years ago`;
}

function getUserInitials(name: string): string {
  if (!name) return '?';
  const parts = name.split(' ');
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return name.substring(0, 2).toUpperCase();
}

function RequestRow({ request, index }: { request: BookRequest; index: number }) {
  const queryClient = useQueryClient();
  const { isAdmin } = useUser();
  const status = statusConfig[request.status] ?? statusConfig.requested;
  const StatusIcon = status.icon;
  const isProcessing = request.status === 'processing';

  const deleteMutation = useMutation({
    mutationFn: () => requestsApi.delete(Number(request.id)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['requests'] });
      queryClient.invalidateQueries({ queryKey: ['request-stats'] });
      toast.success('Request deleted');
    },
    onError: (error: Error) => {
      toast.error('Failed to delete request', {
        description: error.message,
      });
    },
  });

  const markAvailableMutation = useMutation({
    mutationFn: () => requestsApi.update(Number(request.id), { status: 'available' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['requests'] });
      queryClient.invalidateQueries({ queryKey: ['request-stats'] });
      toast.success('Request marked as available');
    },
    onError: (error: Error) => {
      toast.error('Failed to mark request as available', {
        description: error.message,
      });
    },
  });

  const handleBookClick = () => {
    const bookIdentifier = request.book.hardcoverId || request.book.hardcoverSlug;
    if (bookIdentifier) {
      window.location.href = `/book/${bookIdentifier}`;
    } else {
      toast.error('Book ID not available');
    }
  };

  return (
    <div
      className="group relative overflow-hidden rounded-2xl bg-card border border-border/50 hover:border-primary/30 transition-[border-color] duration-300 animate-fade-in-up"
      style={{ animationDelay: `${index * 50}ms` }}
    >
      {/* Subtle background image (hidden on mobile for performance) */}
      <div
        className="absolute inset-0 opacity-[0.03] group-hover:opacity-[0.06] transition-opacity duration-500 hidden md:block"
        style={{
          backgroundImage: `url(${request.book.cover})`,
          backgroundSize: 'cover',
          backgroundPosition: 'center',
          filter: 'blur(20px)',
        }}
      />

      <div className="relative flex flex-col md:flex-row gap-6 p-6">
        {/* Book cover */}
        <div className="flex-shrink-0 mx-auto md:mx-0">
          <div
            className="book-cover w-28 h-40 cursor-pointer group/cover"
            onClick={handleBookClick}
          >
            <img
              src={request.book.cover}
              alt={request.book.title}
              loading="lazy"
              className="w-full h-full object-cover transition-transform duration-300 group-hover/cover:scale-105"
            />
          </div>
          <p className="text-xs text-muted-foreground/60 mt-2 text-center font-medium">
            {formatPublicationYear(request.book.publishedDate)}
          </p>
        </div>

        {/* Book info */}
        <div className="flex-1 min-w-0 text-center md:text-left">
          <h3
            className="text-xl font-bold text-foreground mb-1.5 cursor-pointer hover:text-primary transition-colors duration-300 line-clamp-2"
            onClick={handleBookClick}
          >
            {request.book.title}
          </h3>
          <p className="text-sm text-muted-foreground mb-4">{request.book.author}</p>

          {/* Format badge */}
          <Badge variant="outline" className="text-xs capitalize border-border/50 bg-muted/30 mb-4">
            {request.format}
          </Badge>
        </div>

        {/* Status and actions */}
        <div className="flex-shrink-0 w-full md:w-64 space-y-4">
          {/* Status badge */}
          <div>
            <p className="text-xs text-muted-foreground/60 mb-2 uppercase tracking-wider font-medium">Status</p>
            <Badge
              variant="outline"
              className={cn(
                'inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border',
                status.className
              )}
            >
              <StatusIcon className={cn('h-3.5 w-3.5', isProcessing && 'animate-spin')} />
              {status.label}
            </Badge>
          </div>

          {/* Requested by / Import source */}
          <div>
            {request.source === 'booklore_import' ? (
              <>
                <p className="text-xs text-muted-foreground/60 mb-2">
                  Imported {formatRelativeTime(request.createdAt)}
                </p>
                <div className="flex items-center gap-2">
                  <div className="h-8 w-8 rounded-lg bg-emerald-500/15 flex items-center justify-center">
                    <Library className="h-4 w-4 text-emerald-400" />
                  </div>
                  <span className="text-sm text-emerald-400 font-medium">From Booklore</span>
                </div>
              </>
            ) : (
              <>
                <p className="text-xs text-muted-foreground/60 mb-2">
                  Requested {formatRelativeTime(request.createdAt)}
                </p>
                <div className="flex items-center gap-2">
                  <div className="h-8 w-8 rounded-lg bg-primary/15 flex items-center justify-center text-xs font-semibold text-primary">
                    {getUserInitials(request.userName)}
                  </div>
                  <span className="text-sm text-foreground font-medium">{request.userName}</span>
                </div>
              </>
            )}
          </div>

          {/* Modified date */}
          {request.updatedAt && request.updatedAt !== request.createdAt && request.source !== 'booklore_import' && (
            <p className="text-xs text-muted-foreground/60">
              Modified {formatRelativeTime(request.updatedAt)}
            </p>
          )}

          {request.status === 'approved' && (request.autoSearchAttempts || request.nextSearchAt) && (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-left">
              <p className="text-xs font-medium text-amber-400">
                Automatic search attempt {request.autoSearchAttempts ?? 0}
              </p>
              {request.nextSearchAt && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Retry scheduled {new Date(request.nextSearchAt).toLocaleString()}
                </p>
              )}
              {request.lastSearchError && (
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground" title={request.lastSearchError}>
                  {request.lastSearchError}
                </p>
              )}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex flex-col gap-2 pt-2">
            {isProcessing && isAdmin && (
              <Button
                size="sm"
                onClick={() => markAvailableMutation.mutate()}
                disabled={markAvailableMutation.isPending}
                className="w-full h-9 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white shadow-lg shadow-emerald-500/20"
              >
                <CheckCircle2 className="h-4 w-4 mr-2" />
                Mark Available
              </Button>
            )}

            <Button
              variant="outline"
              size="sm"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
              className="w-full h-9 rounded-lg border-rose-500/30 text-rose-400 hover:bg-rose-500/10 hover:border-rose-500/50"
            >
              <Trash2 className="h-4 w-4 mr-2" />
              Delete
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Requests() {
  const [activeTab, setActiveTab] = useState('all');
  const [page, setPage] = useState(0);
  const { isAdmin } = useUser();

  const statusFilter = activeTab === 'all'
    ? undefined
    : activeTab === 'pending'
    ? 'pending'
    : activeTab === 'approved'
    ? 'approved,processing'
    : activeTab;

  const { data: pageData, isLoading } = useQuery({
    queryKey: ['requests', statusFilter, page],
    queryFn: () => requestsApi.getPage(page * PAGE_SIZE, PAGE_SIZE, statusFilter),
    refetchInterval: (query) => {
      const data = query.state.data?.items;
      const hasProcessing = Array.isArray(data) && data.some((req) => (req as RawBookRequest).status === 'processing');
      return hasProcessing ? 10000 : false;
    },
  });

  const { data: stats } = useQuery({
    queryKey: ['request-stats'],
    queryFn: requestsApi.getStats,
    refetchInterval: 30000,
  });

  const requests = (pageData?.items ?? []) as RawBookRequest[];

  const transformedRequests: BookRequest[] = requests
    .filter((req): req is RawBookRequest & { book: RawRequestBook } => Boolean(req.book))
    .map((req) => ({
      id: String(req.id),
      bookId: String(req.book?.hardcover_id || req.book_id),
      book: {
        id: String(req.book.id),
        title: req.book.title || 'Unknown Title',
        author: req.book.author || 'Unknown Author',
        cover: req.book.cover_url || '/placeholder.svg',
        description: req.book.description || '',
        publishedDate: req.book.published_date || '',
        genres: req.book.genres ? (typeof req.book.genres === 'string' ? req.book.genres.split(',') : req.book.genres) : [],
        rating: req.book.rating || 0,
        series: req.book.series,
        seriesPosition: req.book.series_position,
        hardcoverId: req.book.hardcover_id,
        hardcoverSlug: req.book.hardcover_slug,
        isbn: req.book.isbn,
        pageCount: req.book.page_count,
      },
      userId: String(req.user_id),
      userName: req.user?.username || req.user?.full_name || 'Unknown User',
      format: req.format,
      status: req.status,
      source: req.source || 'user_request',
      notes: req.notes,
      adminNotes: req.admin_notes,
      autoSearchAttempts: req.auto_search_attempts,
      lastSearchAt: req.last_search_at,
      nextSearchAt: req.next_search_at,
      lastSearchError: req.last_search_error,
      downloadTaskId: req.download_task_id,
      createdAt: req.created_at,
      updatedAt: req.updated_at,
    }));
  const filteredRequests = transformedRequests;
  const totalPages = Math.max(1, Math.ceil((pageData?.total ?? 0) / PAGE_SIZE));

  const chooseTab = (value: string) => {
    setActiveTab(value);
    setPage(0);
  };

  const statCards = [
    { label: 'Total', value: stats?.total ?? 0, tab: 'all', icon: Library },
    { label: 'Pending approval', value: stats?.pending ?? 0, tab: 'pending', icon: Clock },
    { label: 'Approved / queued', value: stats?.approved ?? 0, tab: 'approved', icon: CheckCircle },
    { label: 'Eligible now', value: stats?.eligible_for_auto_search ?? 0, tab: 'approved', icon: Search },
    { label: 'Retry scheduled', value: stats?.waiting_for_retry ?? 0, tab: 'approved', icon: RotateCw },
    { label: 'Processing', value: stats?.processing ?? 0, tab: 'processing', icon: Loader2 },
    { label: 'Available', value: stats?.available ?? 0, tab: 'available', icon: CheckCircle2 },
    { label: 'Not found', value: stats?.not_found ?? 0, tab: 'not_found', icon: XCircle },
    { label: 'Denied', value: stats?.denied ?? 0, tab: 'denied', icon: XCircle },
    { label: 'Processed', value: stats?.processed ?? 0, tab: 'all', icon: CheckCircle },
  ];

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="relative">
        <div className="flex items-center gap-3 mb-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 border border-primary/20">
            <Clock className="h-5 w-5 text-primary" />
          </div>
          <span className="text-sm font-medium text-primary uppercase tracking-wider">Library</span>
        </div>
        <h1 className="text-4xl font-bold text-foreground tracking-tight">
          Requests
        </h1>
        <p className="mt-2 text-muted-foreground">
          Track the status of your book requests
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        {statCards.map(({ label, value, tab, icon: Icon }) => (
          <button
            key={label}
            type="button"
            onClick={() => chooseTab(tab)}
            className="rounded-xl border border-border/50 bg-card p-4 text-left transition-colors hover:border-primary/40 hover:bg-card/80"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground">{label}</span>
              <Icon className="h-4 w-4 text-primary" />
            </div>
            <p className="mt-2 text-2xl font-bold text-foreground">{value}</p>
          </button>
        ))}
      </div>

      {isAdmin && <AutomationControls />}

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={chooseTab}>
        <TabsList className="h-auto flex-wrap p-1.5 bg-card/50 border border-border/50 rounded-xl">
          <TabsTrigger value="all" className="h-9 px-4 rounded-lg data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
            All
          </TabsTrigger>
          <TabsTrigger value="pending" className="h-9 px-4 rounded-lg data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
            Pending
          </TabsTrigger>
          <TabsTrigger value="approved" className="h-9 px-4 rounded-lg data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
            Approved
          </TabsTrigger>
          <TabsTrigger value="processing" className="h-9 px-4 rounded-lg data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
            Processing
          </TabsTrigger>
          <TabsTrigger value="available" className="h-9 px-4 rounded-lg data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
            Available
          </TabsTrigger>
          <TabsTrigger value="not_found" className="h-9 px-4 rounded-lg data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
            Not Found
          </TabsTrigger>
          <TabsTrigger value="denied" className="h-9 px-4 rounded-lg data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
            Denied
          </TabsTrigger>
        </TabsList>

        <TabsContent value={activeTab} className="mt-8">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center py-16">
              <Loader2 className="h-10 w-10 animate-spin text-primary mb-4" />
              <p className="text-muted-foreground font-medium">Loading requests...</p>
            </div>
          ) : filteredRequests.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16">
              <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-muted/30 mb-6">
                <Inbox className="h-10 w-10 text-muted-foreground/50" />
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-2">No requests found</h3>
              <p className="text-muted-foreground text-center max-w-sm">
                Start browsing the catalog and request some books to build your library.
              </p>
              <Button asChild className="mt-6 h-11 px-6 rounded-xl">
                <Link to="/">Discover Books</Link>
              </Button>
            </div>
          ) : (
            <div className="space-y-6">
              <div className="space-y-4">
                {filteredRequests.map((request, index) => (
                  <RequestRow key={request.id} request={request} index={index} />
                ))}
              </div>
              {totalPages > 1 && (
                <div className="flex items-center justify-between rounded-xl border border-border/50 bg-card/50 p-3">
                  <p className="text-sm text-muted-foreground">
                    Page {page + 1} of {totalPages} · {pageData?.total ?? 0} requests
                  </p>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>
                      Previous
                    </Button>
                    <Button variant="outline" size="sm" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => value + 1)}>
                      Next
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
