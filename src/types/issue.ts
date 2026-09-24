export type MediaIssueType =
  | 'wrong_language'
  | 'incomplete'
  | 'wrong_content'
  | 'file_structure'
  | 'unplayable'
  | 'quality'
  | 'metadata'
  | 'other';

export type MediaFormat = 'ebook' | 'audiobook';

export interface MediaIssueBook {
  id: number;
  title: string;
  author: string;
  cover_url?: string | null;
  hardcover_id?: number | null;
  booklore_id?: number | null;
  audiobookshelf_id?: string | null;
}

export interface MediaIssue {
  public_id: string;
  reference: string;
  book: MediaIssueBook | null;
  format: MediaFormat | null;
  issue_type: MediaIssueType;
  status: 'open' | 'done';
  is_critical: boolean;
  report_count: number;
  report_text: string | null;
  critical_explanation?: string | null;
  submitted_at: string;
  last_reported_at: string;
  done_at?: string | null;
  done_note?: string | null;
}

export interface AdminMediaIssue extends Omit<MediaIssue, 'report_text' | 'submitted_at'> {
  critical_report_count: number;
  critical_first_reported_at?: string | null;
  first_reported_at: string;
  download?: {
    id: number;
    release_title?: string | null;
    source?: string | null;
    indexer?: string | null;
    state?: string | null;
    import_status?: string | null;
    final_path?: string | null;
    download_path?: string | null;
    completed_at?: string | null;
  } | null;
  reports?: Array<{
    id: number;
    reporter_user_id?: number | null;
    reporter_name: string;
    report_text: string;
    is_critical: boolean;
    critical_explanation?: string | null;
    request_id?: number | null;
    download_task_id?: number | null;
    created_at: string;
    updated_at?: string | null;
  }>;
}

export interface IssueNotification {
  id: number;
  kind: 'done' | 'reopened';
  message: string;
  read_at?: string | null;
  created_at: string;
  issue: {
    public_id: string;
    reference: string;
    status: 'open' | 'done';
    book_title?: string | null;
    format?: MediaFormat | null;
    issue_type: MediaIssueType;
  };
}

export const ISSUE_TYPE_LABELS: Record<MediaIssueType, string> = {
  wrong_language: 'Wrong language',
  incomplete: 'Missing or incomplete',
  wrong_content: 'Wrong book or edition',
  file_structure: 'Chapter or file organization',
  unplayable: 'Will not open or play',
  quality: 'Poor quality',
  metadata: 'Incorrect metadata or cover',
  other: 'Other',
};
