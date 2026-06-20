import React, { useRef, useState } from 'react';
import { Button, Card, Field, Toast } from '@keel/ui';
import { uploadDocument } from '../api';

type ToastState = { message: string; kind: 'success' | 'error' } | null;

export function RagUpload() {
  const [file, setFile] = useState<File | null>(null);
  const [chunkType, setChunkType] = useState<string>('policy');
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<ToastState>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [lastUpload, setLastUpload] = useState<{ filename: string; chunks: number } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

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
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Upload failed', kind: 'error' });
    } finally {
      setLoading(false);
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

      {toast && <Toast message={toast.message} kind={toast.kind} onClose={() => setToast(null)} />}
    </div>
  );
}
