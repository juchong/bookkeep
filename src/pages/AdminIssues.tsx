import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Flag, Loader2, RotateCcw, Search, Wrench } from 'lucide-react';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { adminMediaIssuesApi } from '@/lib/api';
import { ISSUE_TYPE_LABELS, MediaFormat, MediaIssueType } from '@/types/issue';

export default function AdminIssues() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<'open' | 'done' | 'all'>('open');
  const [format, setFormat] = useState<MediaFormat | 'all'>('all');
  const [issueType, setIssueType] = useState<MediaIssueType | 'all'>('all');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [doneNote, setDoneNote] = useState('');

  const { data: stats } = useQuery({
    queryKey: ['admin-media-issues', 'stats'],
    queryFn: adminMediaIssuesApi.getStats,
    refetchInterval: 60_000,
  });
  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-media-issues', status, format, issueType, search],
    queryFn: () => adminMediaIssuesApi.getPage({
      status,
      format: format === 'all' ? undefined : format,
      issueType: issueType === 'all' ? undefined : issueType,
      search: search || undefined,
      limit: 100,
    }),
  });
  const { data: selected, isLoading: selectedLoading } = useQuery({
    queryKey: ['admin-media-issue', selectedId],
    queryFn: () => adminMediaIssuesApi.getById(selectedId as string),
    enabled: Boolean(selectedId),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['admin-media-issues'] });
    queryClient.invalidateQueries({ queryKey: ['admin-media-issue'] });
    queryClient.invalidateQueries({ queryKey: ['media-issues'] });
  };
  const doneMutation = useMutation({
    mutationFn: () => adminMediaIssuesApi.markDone(selectedId as string, doneNote.trim() || undefined),
    onSuccess: () => { invalidate(); setSelectedId(null); setDoneNote(''); toast.success('Issue marked done'); },
    onError: (err: Error) => toast.error(err.message),
  });
  const reopenMutation = useMutation({
    mutationFn: () => adminMediaIssuesApi.reopen(selectedId as string),
    onSuccess: () => { invalidate(); setSelectedId(null); toast.success('Issue reopened'); },
    onError: (err: Error) => toast.error(err.message),
  });
  const criticalMutation = useMutation({
    mutationFn: (isCritical: boolean) => adminMediaIssuesApi.update(selectedId as string, { is_critical: isCritical }),
    onSuccess: invalidate,
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <div className="space-y-7">
      <div>
        <div className="mb-2 flex items-center gap-2 text-sm font-medium uppercase tracking-wider text-primary">
          <Wrench className="h-4 w-4" /> Repair list
        </div>
        <h1 className="text-3xl font-bold">Media issues</h1>
        <p className="mt-2 text-muted-foreground">Fix the affected media, then mark the report done.</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <button onClick={() => setStatus('open')} className="rounded-xl border border-border bg-card p-4 text-left hover:border-primary/40">
          <p className="text-sm text-muted-foreground">Open</p><p className="mt-1 text-3xl font-bold">{stats?.open ?? 0}</p>
        </button>
        <button onClick={() => setStatus('open')} className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-left">
          <p className="flex items-center gap-2 text-sm text-destructive"><AlertTriangle className="h-4 w-4" />Critical</p>
          <p className="mt-1 text-3xl font-bold text-destructive">{stats?.critical ?? 0}</p>
        </button>
        <button onClick={() => setStatus('done')} className="rounded-xl border border-border bg-card p-4 text-left hover:border-primary/40">
          <p className="text-sm text-muted-foreground">Done</p><p className="mt-1 text-3xl font-bold">{stats?.done ?? 0}</p>
        </button>
      </div>

      <div className="flex flex-wrap gap-3 rounded-xl border border-border bg-card p-4">
        <form className="flex min-w-64 flex-1 gap-2" onSubmit={(event) => { event.preventDefault(); setSearch(searchInput.trim()); }}>
          <Input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="Search title, author, or reference" />
          <Button type="submit" variant="outline" aria-label="Search"><Search className="h-4 w-4" /></Button>
        </form>
        <Select value={status} onValueChange={(value) => setStatus(value as typeof status)}>
          <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="open">Open</SelectItem><SelectItem value="done">Done</SelectItem><SelectItem value="all">All</SelectItem></SelectContent>
        </Select>
        <Select value={format} onValueChange={(value) => setFormat(value as typeof format)}>
          <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="all">All formats</SelectItem><SelectItem value="ebook">Ebook</SelectItem><SelectItem value="audiobook">Audiobook</SelectItem></SelectContent>
        </Select>
        <Select value={issueType} onValueChange={(value) => setIssueType(value as typeof issueType)}>
          <SelectTrigger className="w-56"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All problems</SelectItem>
            {Object.entries(ISSUE_TYPE_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      <div className="overflow-hidden rounded-xl border border-border">
        {isLoading ? (
          <div className="p-12 text-center text-muted-foreground">Loading repair list…</div>
        ) : error ? (
          <div className="p-12 text-center text-destructive">{(error as Error).message}</div>
        ) : !data?.items.length ? (
          <div className="p-12 text-center text-muted-foreground">No matching media issues.</div>
        ) : (
          <Table>
            <TableHeader><TableRow><TableHead>Media</TableHead><TableHead>Problem</TableHead><TableHead>Reports</TableHead><TableHead>First reported</TableHead><TableHead className="text-right">Action</TableHead></TableRow></TableHeader>
            <TableBody>
              {data.items.map((issue) => (
                <TableRow key={issue.public_id} className={issue.is_critical ? 'bg-destructive/5' : ''}>
                  <TableCell>
                    <button className="flex items-center gap-3 text-left" onClick={() => { setSelectedId(issue.public_id); setDoneNote(''); }}>
                      <img src={issue.book?.cover_url || '/placeholder.svg'} alt="" className="h-14 w-10 rounded object-cover" />
                      <div><p className="font-medium">{issue.book?.title || 'Other Bookkeep problem'}</p><p className="text-xs text-muted-foreground">{issue.format || 'General'} · {issue.reference}</p></div>
                    </button>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-2">
                      {issue.is_critical && <Badge variant="destructive"><AlertTriangle className="mr-1 h-3 w-3" />Critical</Badge>}
                      <Badge variant="outline">{ISSUE_TYPE_LABELS[issue.issue_type]}</Badge>
                    </div>
                  </TableCell>
                  <TableCell>{issue.report_count}</TableCell>
                  <TableCell>{new Date(issue.first_reported_at).toLocaleDateString()}</TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" variant={issue.status === 'open' ? 'default' : 'outline'} onClick={() => { setSelectedId(issue.public_id); setDoneNote(''); }}>
                      {issue.status === 'open' ? <><CheckCircle2 className="h-4 w-4" />Fix / Done</> : 'View'}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <Dialog open={Boolean(selectedId)} onOpenChange={(open) => { if (!open) setSelectedId(null); }}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          {selectedLoading || !selected ? (
            <div className="flex justify-center p-10"><Loader2 className="h-6 w-6 animate-spin" /></div>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle className="flex flex-wrap items-center gap-2">
                  {selected.book?.title || 'Other Bookkeep problem'}
                  {selected.is_critical && <Badge variant="destructive">Critical</Badge>}
                </DialogTitle>
                <DialogDescription>{selected.reference} · {selected.format || 'General'} · {ISSUE_TYPE_LABELS[selected.issue_type]}</DialogDescription>
              </DialogHeader>

              <div className="space-y-5">
                <div className="flex flex-wrap gap-2">
                  {selected.book?.hardcover_id && (
                    <Button variant="outline" size="sm" asChild>
                      <Link to={`/book/${selected.book.hardcover_id}`}>Open book</Link>
                    </Button>
                  )}
                  {selected.download && (
                    <Button variant="outline" size="sm" asChild>
                      <Link to="/downloads">Open downloads</Link>
                    </Button>
                  )}
                </div>
                {selected.is_critical && (
                  <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <p className="font-medium text-destructive">Critical content report</p>
                      <Button size="sm" variant="outline" onClick={() => criticalMutation.mutate(false)} disabled={criticalMutation.isPending}>Remove critical flag</Button>
                    </div>
                  </div>
                )}

                <div className="space-y-3">
                  <h3 className="font-semibold">Reports</h3>
                  {selected.reports?.map((report) => (
                    <div key={report.id} className="rounded-xl border border-border bg-muted/20 p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="font-medium">{report.reporter_name}</p>
                        <p className="text-xs text-muted-foreground">{new Date(report.created_at).toLocaleString()}</p>
                      </div>
                      <p className="mt-3 whitespace-pre-wrap text-sm">{report.report_text}</p>
                      {report.critical_explanation && (
                        <p className="mt-3 rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{report.critical_explanation}</p>
                      )}
                    </div>
                  ))}
                </div>

                {selected.download && (
                  <div className="rounded-xl border border-border p-4 text-sm">
                    <h3 className="font-semibold">Media source</h3>
                    <dl className="mt-3 grid gap-2 sm:grid-cols-2">
                      <div><dt className="text-muted-foreground">Release</dt><dd className="break-all">{selected.download.release_title || 'Unknown'}</dd></div>
                      <div><dt className="text-muted-foreground">Source</dt><dd>{selected.download.indexer || selected.download.source || 'Unknown'}</dd></div>
                      <div><dt className="text-muted-foreground">Download</dt><dd>#{selected.download.id} · {selected.download.state}</dd></div>
                      <div><dt className="text-muted-foreground">Import</dt><dd>{selected.download.import_status || 'Unknown'}</dd></div>
                    </dl>
                    {(selected.download.final_path || selected.download.download_path) && (
                      <div className="mt-3"><p className="text-muted-foreground">Path</p><code className="break-all text-xs">{selected.download.final_path || selected.download.download_path}</code></div>
                    )}
                  </div>
                )}

                {selected.status === 'open' ? (
                  <div className="space-y-2">
                    <Label htmlFor="done-note">Completion note (optional)</Label>
                    <Textarea id="done-note" value={doneNote} onChange={(event) => setDoneNote(event.target.value)} maxLength={1000} rows={3} placeholder="For example: Replaced with the English release." />
                  </div>
                ) : selected.done_note ? (
                  <div className="rounded-xl bg-success/5 p-4"><p className="font-medium text-success">Completed</p><p className="mt-1 text-sm">{selected.done_note}</p></div>
                ) : null}
              </div>

              <DialogFooter>
                <Button variant="outline" onClick={() => setSelectedId(null)}>Close</Button>
                {selected.status === 'open' ? (
                  <Button onClick={() => doneMutation.mutate()} disabled={doneMutation.isPending}>
                    {doneMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}<CheckCircle2 className="h-4 w-4" />Mark Done
                  </Button>
                ) : (
                  <Button onClick={() => reopenMutation.mutate()} disabled={reopenMutation.isPending}>
                    <RotateCcw className="h-4 w-4" />Reopen
                  </Button>
                )}
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
