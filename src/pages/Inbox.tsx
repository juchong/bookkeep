import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCheck, CheckCircle2, RotateCcw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { notificationsApi } from '@/lib/api';

export default function Inbox() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['issue-notifications'],
    queryFn: () => notificationsApi.getPage(0, 100),
    refetchInterval: 60_000,
  });
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['issue-notifications'] });
    queryClient.invalidateQueries({ queryKey: ['issue-notifications-unread'] });
  };
  const readMutation = useMutation({
    mutationFn: (id: number) => notificationsApi.markRead(id),
    onSuccess: invalidate,
  });
  const readAllMutation = useMutation({
    mutationFn: notificationsApi.markAllRead,
    onSuccess: invalidate,
  });

  const openNotification = async (id: number, publicId: string, unread: boolean) => {
    if (unread) await readMutation.mutateAsync(id);
    navigate(`/issues/${publicId}`);
  };

  return (
    <div className="space-y-7">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2 text-sm font-medium uppercase tracking-wider text-primary">
            <Bell className="h-4 w-4" /> Updates
          </div>
          <h1 className="text-3xl font-bold">Inbox</h1>
          <p className="mt-2 text-muted-foreground">Updates about media problems you reported.</p>
        </div>
        {!!data?.items.some((item) => !item.read_at) && (
          <Button variant="outline" onClick={() => readAllMutation.mutate()} disabled={readAllMutation.isPending}>
            <CheckCheck className="h-4 w-4" /> Mark all read
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="p-10 text-center text-muted-foreground">Loading notifications…</div>
      ) : error ? (
        <div className="p-10 text-center text-destructive">{(error as Error).message}</div>
      ) : !data?.items.length ? (
        <div className="rounded-xl border border-dashed border-border p-12 text-center">
          <Bell className="mx-auto h-10 w-10 text-muted-foreground/50" />
          <p className="mt-4 font-medium">No updates yet</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          {data.items.map((notification) => (
            <button
              key={notification.id}
              type="button"
              onClick={() => openNotification(notification.id, notification.issue.public_id, !notification.read_at)}
              className={`flex w-full items-start gap-4 border-b border-border p-4 text-left last:border-b-0 hover:bg-muted/30 ${!notification.read_at ? 'bg-primary/5' : ''}`}
            >
              <div className={`mt-0.5 rounded-full p-2 ${notification.kind === 'done' ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning'}`}>
                {notification.kind === 'done' ? <CheckCircle2 className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="font-medium">{notification.message}</p>
                  {!notification.read_at && <span className="h-2 w-2 rounded-full bg-primary" />}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {notification.issue.reference} · {new Date(notification.created_at).toLocaleString()}
                </p>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
