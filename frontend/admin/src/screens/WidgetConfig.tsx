import { useEffect, useState } from 'react';
import { Button, Card, Field, Input, Textarea, Toast, Spinner } from '@keel/ui';
import { getWidgetConfig, putWidgetConfig } from '../api';
import type { AuthHeaders, WidgetConfig as IWidgetConfig } from '../api';

interface Props {
  auth: AuthHeaders;
}

type ToastState = { message: string; kind: 'success' | 'error' } | null;

export function WidgetConfig({ auth }: Props) {
  const [personaPrompt, setPersonaPrompt] = useState('');
  const [personaName, setPersonaName] = useState('');
  const [allowedOrigins, setAllowedOrigins] = useState('');
  const [enabledTools, setEnabledTools] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<ToastState>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const cfg = await getWidgetConfig(auth);
        if (cancelled) return;
        setPersonaPrompt(cfg.persona_prompt ?? '');
        setPersonaName(cfg.persona_name ?? '');
        setAllowedOrigins((cfg.allowed_origins ?? []).join(', '));
        setEnabledTools((cfg.enabled_tools ?? []).join(', '));
      } catch (err) {
        if (cancelled) return;
        setToast({
          message: err instanceof Error ? err.message : 'Failed to load config',
          kind: 'error',
        });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [auth]);

  async function handleSave() {
    setSaving(true);
    try {
      const payload: IWidgetConfig = {
        persona_prompt: personaPrompt,
        persona_name: personaName,
        allowed_origins: allowedOrigins
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        enabled_tools: enabledTools
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
      };
      await putWidgetConfig(auth, payload);
      setToast({ message: 'Configuration saved', kind: 'success' });
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Save failed',
        kind: 'error',
      });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: 200,
          color: 'var(--text-muted)',
          gap: 'var(--sp-3)',
        }}
      >
        <Spinner size={20} />
        <span style={{ fontFamily: "'Inter', system-ui, sans-serif", fontSize: 'var(--text-sm)' }}>
          Loading configuration…
        </span>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 640, margin: '0 auto' }}>
      <h1
        style={{
          fontFamily: "'Fraunces', Georgia, serif",
          fontSize: 'var(--text-2xl)',
          color: 'var(--text)',
          marginBottom: 'var(--sp-6)',
        }}
      >
        Widget Configuration
      </h1>

      <Card style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
        <Field label="Persona prompt">
          <Textarea
            rows={4}
            value={personaPrompt}
            onChange={(e) => setPersonaPrompt(e.target.value)}
            placeholder="Describe how Keel's AI should introduce itself and behave…"
          />
        </Field>

        <Field label="Persona name">
          <Input
            value={personaName}
            onChange={(e) => setPersonaName(e.target.value)}
            placeholder="Keel"
          />
        </Field>

        <Field label="Allowed origins (comma-separated)">
          <Input
            value={allowedOrigins}
            onChange={(e) => setAllowedOrigins(e.target.value)}
            placeholder="https://sis.university.edu, https://portal.university.edu"
          />
        </Field>

        <Field label="Enabled tools (comma-separated)">
          <Input
            value={enabledTools}
            onChange={(e) => setEnabledTools(e.target.value)}
            placeholder="propose_plan, predict_risk, search_sections"
          />
        </Field>

        {/* Safety rails notice */}
        <div
          style={{
            background: 'var(--bg)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            padding: 'var(--sp-3) var(--sp-4)',
            fontSize: 'var(--text-sm)',
            color: 'var(--text-muted)',
            fontFamily: "'Inter', system-ui, sans-serif",
          }}
        >
          Safety rails are enforced in code and cannot be changed here.
        </div>

        <Button onClick={handleSave} loading={saving}>
          Save configuration
        </Button>
      </Card>

      {toast && (
        <Toast
          message={toast.message}
          kind={toast.kind}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}
