/**
 * StagedSearch — visually convincing section search form, but inert.
 * Shows a Toast directing the user to the Keel advisor chat.
 */

import React, { useState } from 'react';
import { Button, Field, Input, Toast } from '@keel/ui';

export function StagedSearch() {
  const [courseCode, setCourseCode] = useState('');
  const [term, setTerm] = useState('');
  const [toastVisible, setToastVisible] = useState(false);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setToastVisible(true);
  }

  return (
    <div>
      <h2 className="page-heading">Section Search</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', marginBottom: '24px' }}>
        Search for open sections by course code and term.
      </p>

      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '6px',
          padding: '24px',
          maxWidth: '440px',
        }}
      >
        <form onSubmit={handleSearch} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <Field label="Course Code">
            <Input
              type="text"
              placeholder="e.g. CS301"
              value={courseCode}
              onChange={(e) => setCourseCode(e.target.value)}
            />
          </Field>

          <Field label="Term">
            <Input
              type="text"
              placeholder="e.g. Fall 2025"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
            />
          </Field>

          <Button type="submit" variant="primary">
            Search Sections
          </Button>
        </form>
      </div>

      {toastVisible && (
        <Toast
          message="Section search is handled by the Keel advisor — click the chat icon in the bottom-right corner."
          kind="info"
          onClose={() => setToastVisible(false)}
        />
      )}
    </div>
  );
}
