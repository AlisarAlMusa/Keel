import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Card, Field, Modal, Spinner, Table, Textarea, Toast } from '@keel/ui';
import {
  deleteRagDocument,
  getRagDocument,
  listRagDocuments,
  updateRagDocument,
  uploadDocument,
  type RagDocument,
} from '../api';

type ToastState = { message: string; kind: 'success' | 'error' } | null;

export function RagUpload() {
  const [file, setFile] = useState<File | null>(null);
  const [chunkType, setChunkType] = useState<string>('policy');
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<ToastState>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [lastUpload, setLastUpload] = useState<{ filename: string; chunks: number } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Document management
  const [docs, setDocs] = useState<RagDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(true);
  const [editing, setEditing] = useState<{ filename: string; content: string } | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [busyDoc, setBusyDoc] = useState<string | null>(null);

  const loadDocs = useCallback(async () => {
    setDocsLoading(true);
    try {
      setDocs(await listRagDocuments());
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed to load documents', kind: 'error' });
    } finally {
      setDocsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) setFile(dropped);
  }

  async function handleUpload() {
    if (!file) return;
    setLoading(true);
    try {
      const result = await uploadDocument(file, chunkType);
      setLastUpload({ filename: file.name, chunks: result.chunks_estimated });
      setToast({ message: `Uploaded — ~${result.chunks_estimated} chunks queued for embedding`, kind: 'success' });
      setFile(null);
      if (inputRef.current) inputRef.current.value = '';
      // Re-indexing is async on the worker; give it a moment, then refresh.
      setTimeout(() => void loadDocs(), 1500);
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Upload failed', kind: 'error' });
    } finally {
      setLoading(false);
    }
  }

  async function handleView(filename: string) {
    setBusyDoc(filename);
    try {
      const doc = await getRagDocument(filename);
      setEditing({ filename: doc.filename, content: doc.content });
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed to open document', kind: 'error' });
    } finally {
      setBusyDoc(null);
    }
  }

  async function handleSaveEdit() {
    if (!editing) return;
    setEditSaving(true);
    try {
      await updateRagDocument(editing.filename, editing.content);
      setToast({ message: `Saved — re-indexing ${editing.filename}…`, kind: 'success' });
      setEditing(null);
      setTimeout(() => void loadDocs(), 1500);
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Save failed', kind: 'error' });
    } finally {
      setEditSaving(false);
    }
  }

  async function handleDelete(filename: string) {
    if (!window.confirm(`Delete "${filename}" from the knowledge base? This removes it from both storage and the search index.`)) {
      return;
    }
    setBusyDoc(filename);
    try {
      const r = await deleteRagDocument(filename);
      setToast({ message: `Deleted ${filename} (${r.deleted_chunks} chunks removed)`, kind: 'success' });
      await loadDocs();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Delete failed', kind: 'error' });
    } finally {
      setBusyDoc(null);
    }
  }

  return (
    <div style={{ maxWidth: 640, margin: '0 auto' }}>
      <h1 style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 'var(--text-2xl)', color: 'var(--text)', marginBottom: 'var(--sp-2)' }}>
        Knowledge Base
      </h1>
      <p style={{ color: 'var(--text-muted)', fontSize: 'var(--text-sm)', marginBottom: 'var(--sp-6)' }}>
        Upload prose documents (catalog.md, policy.md, handbooks). Structured data like course listings is seeded separately.
      </p>

      <Card style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
        <div
          onDrop={handleDrop}
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onClick={() => inputRef.current?.click()}
          style={{
            border: `2px dashed ${isDragging ? 'var(--accent)' : 'var(--border)'}`,
            borderRadius: 'var(--radius-lg)',
            padding: 'var(--sp-8)',
            textAlign: 'center',
            cursor: 'pointer',
            background: isDragging ? 'rgba(91,194,231,0.06)' : 'var(--bg)',
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".md,.txt,.pdf"
            style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) setFile(f); }}
          />
          <div style={{ fontSize: 'var(--text-2xl)', marginBottom: 'var(--sp-2)', color: 'var(--text-muted)' }}>
            {file ? '📄' : '⬆'}
          </div>
          {file ? (
            <div>
              <span style={{ fontFamily: "'Inter', system-ui, sans-serif", fontWeight: 600, color: 'var(--text)', fontSize: 'var(--text-sm)' }}>{file.name}</span>
              <br />
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>{(file.size / 1024).toFixed(1)} KB — click to change</span>
            </div>
          ) : (
            <div>
              <span style={{ fontFamily: "'Inter', system-ui, sans-serif", color: 'var(--text-muted)', fontSize: 'var(--text-sm)' }}>
                Drag &amp; drop a file here, or <span style={{ color: 'var(--accent)', fontWeight: 600 }}>browse</span>
              </span>
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginTop: 'var(--sp-1)' }}>Accepts .md, .txt, .pdf</div>
            </div>
          )}
        </div>

        <Field label="Chunk type">
          <select
            value={chunkType}
            onChange={(e) => setChunkType(e.target.value)}
            style={{ background: 'var(--input-bg)', color: 'var(--input-text)', border: '1px solid var(--input-border)', borderRadius: 'var(--radius-md)', padding: 'var(--sp-2) var(--sp-3)', fontSize: 'var(--text-base)', fontFamily: "'Inter', system-ui, sans-serif", outline: 'none', width: '100%', cursor: 'pointer' }}
          >
            <option value="policy">policy</option>
            <option value="course">course</option>
          </select>
        </Field>

        <Button onClick={handleUpload} disabled={!file} loading={loading}>Upload document</Button>

        {lastUpload && (
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', borderTop: '1px solid var(--border)', paddingTop: 'var(--sp-3)' }}>
            Last upload: <strong>{lastUpload.filename}</strong> — ~{lastUpload.chunks} chunks queued
          </div>
        )}
      </Card>

      {/* Indexed documents — list / view-edit / delete to avoid contradictory prose */}
      <div style={{ marginTop: 'var(--sp-6)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 style={{ fontFamily: "'Fraunces', Georgia, serif", fontSize: 'var(--text-xl)', color: 'var(--text)', margin: 0 }}>
          Indexed documents
        </h2>
        <Button variant="secondary" onClick={() => void loadDocs()}>Refresh</Button>
      </div>
      <p style={{ color: 'var(--text-muted)', fontSize: 'var(--text-xs)', margin: 'var(--sp-1) 0 var(--sp-3)' }}>
        These are the files the advisor retrieves over. Delete or edit an old file before
        uploading a newer one on the same topic — otherwise both are searched and may contradict.
      </p>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        {docsLoading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)', padding: 'var(--sp-6)', color: 'var(--text-muted)' }}>
            <Spinner size={18} /> Loading…
          </div>
        ) : (
          <Table
            headers={['Document', 'Chunks', 'Updated', 'Actions']}
            emptyMessage="No documents indexed yet — upload one above."
            rows={docs.map((d) => [
              <strong key="f" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>{d.filename}</strong>,
              <span key="c">{d.chunks}</span>,
              <span key="u" style={{ color: 'var(--text-muted)', fontSize: 'var(--text-xs)' }}>
                {d.updated_at ? new Date(d.updated_at).toLocaleString() : '—'}
              </span>,
              <div key="a" style={{ display: 'flex', gap: 'var(--sp-2)' }}>
                <Button variant="secondary" onClick={() => void handleView(d.filename)} loading={busyDoc === d.filename}>
                  View / Edit
                </Button>
                <Button variant="danger" onClick={() => void handleDelete(d.filename)} disabled={busyDoc === d.filename}>
                  Delete
                </Button>
              </div>,
            ])}
          />
        )}
      </Card>

      <Modal
        open={editing !== null}
        title={editing ? `Edit: ${editing.filename}` : ''}
        onClose={() => setEditing(null)}
        width={720}
      >
        {editing && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
            <p style={{ color: 'var(--text-muted)', fontSize: 'var(--text-xs)', margin: 0 }}>
              Saving replaces the file and re-indexes it — old chunks for this file are removed,
              so the search index matches exactly what you save here.
            </p>
            <Textarea
              value={editing.content}
              onChange={(e) => setEditing({ ...editing, content: e.target.value })}
              rows={18}
              style={{
                fontFamily: 'ui-monospace, Menlo, monospace',
                fontSize: 'var(--text-sm)',
                width: '100%',
                boxSizing: 'border-box',
              }}
            />
            <div style={{ display: 'flex', gap: 'var(--sp-2)', justifyContent: 'flex-end' }}>
              <Button variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
              <Button onClick={() => void handleSaveEdit()} loading={editSaving}>Save &amp; re-index</Button>
            </div>
          </div>
        )}
      </Modal>

      {toast && <Toast message={toast.message} kind={toast.kind} onClose={() => setToast(null)} />}
    </div>
  );
}
