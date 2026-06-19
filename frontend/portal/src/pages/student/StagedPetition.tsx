/**
 * StagedPetition — visually convincing petition form, but inert.
 * Shows a Toast directing the user to the Keel advisor chat.
 */

import React, { useState } from 'react';
import { Button, Field, Toast } from '@keel/ui';

const PETITION_TYPES = [
  'Late Withdrawal',
  'Grade Appeal',
  'Course Substitution',
  'Independent Study',
  'Overload Request',
  'Other',
];

export function StagedPetition() {
  const [type, setType] = useState('');
  const [description, setDescription] = useState('');
  const [toastVisible, setToastVisible] = useState(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setToastVisible(true);
  }

  return (
    <div>
      <h2 className="page-heading">Submit Petition</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', marginBottom: '24px' }}>
        Use this form to submit an academic petition to the Registrar's office.
      </p>

      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '6px',
          padding: '24px',
          maxWidth: '480px',
        }}
      >
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <Field label="Petition Type">
            <select
              value={type}
              onChange={(e) => setType(e.target.value)}
              style={{
                background: 'var(--input-bg)',
                color: type ? 'var(--input-text)' : 'var(--text-muted)',
                border: '1px solid var(--input-border)',
                borderRadius: '4px',
                padding: '8px 12px',
                fontSize: '1rem',
                fontFamily: 'Inter, system-ui, sans-serif',
                width: '100%',
              }}
            >
              <option value="">Select a type…</option>
              {PETITION_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </Field>

          <Field label="Description">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe your petition in detail…"
              rows={5}
              style={{
                background: 'var(--input-bg)',
                color: 'var(--input-text)',
                border: '1px solid var(--input-border)',
                borderRadius: '4px',
                padding: '8px 12px',
                fontSize: '1rem',
                fontFamily: 'Inter, system-ui, sans-serif',
                width: '100%',
                resize: 'vertical',
                outline: 'none',
              }}
            />
          </Field>

          <Button type="submit" variant="primary">
            Submit Petition
          </Button>
        </form>
      </div>

      {toastVisible && (
        <Toast
          message="Petition submission is handled by the Keel advisor — click the chat icon in the bottom-right corner."
          kind="info"
          onClose={() => setToastVisible(false)}
        />
      )}
    </div>
  );
}
