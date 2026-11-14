/**
 * The docked NirnAIn AI panel: greeting, starter prompts, conversation log and question form.
 *
 * Purpose: The PM can ask about the screen they are on; when the assistant is unavailable the
 * panel answers with labelled general guidance from `lib/assistant.ts` instead of inventing
 * project facts. The panel stays mounted while closed so the conversation survives closing and
 * reopening, and Escape inside it closes it.
 * How it fits: Port of the mockup's scripts/views/chat.js (`aside#chat`); rendered by
 * `components/Shell.tsx`; sends questions through `useAssistant` (`POST
 * /api/v1/assistant/messages`); layer: component.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type Ref } from 'react'
import { errorMessage } from '@/lib/api'
import { ASSISTANT_SUGGESTIONS, assistantUnavailable, guidanceReply, MAX_HISTORY, MAX_MESSAGE_CHARS } from '@/lib/assistant'
import { useAssistant } from '@/lib/queries'

interface ChatMessage {
  id: number
  who: 'me' | 'ai'
  text: string
  /** Shown under a reply: general guidance or an error, not an AI answer. */
  note?: string
}

const QUESTION_MAX_CHARS = 500

/** The assistant panel; hidden (not unmounted) when `open` is false. */
export default function AIChat({
  open,
  contextLabel,
  firstName,
  projectId,
  onClose,
  inputRef,
}: {
  open: boolean
  /** Screen name for the greeting and the request's `page`. */
  contextLabel: string
  firstName: string | null
  /** The project in the URL, so answers can use its data (under the caller's access rights). */
  projectId: string | undefined
  onClose: () => void
  inputRef?: Ref<HTMLInputElement>
}) {
  const assistant = useAssistant()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [question, setQuestion] = useState('')
  const nextId = useRef(1)
  const logRef = useRef<HTMLDivElement>(null)
  const headingId = useId()
  const typing = assistant.isPending

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [messages, typing])

  /** Adds a message to the log with the next local id. */
  function append(message: Omit<ChatMessage, 'id'>) {
    setMessages((previous) => [...previous, { ...message, id: nextId.current++ }])
  }

  /**
   * Sends a question with the conversation so far (guidance and error replies left out); a failure becomes a
   * labelled guidance or error reply.
   */
  function ask(text: string) {
    const trimmed = text.trim()
    if (!trimmed || typing) return
    // Guidance and error replies are not the assistant's words; leave them out of the conversation.
    const history = [...messages.filter((message) => !message.note), { id: 0, who: 'me' as const, text: trimmed }]
    append({ who: 'me', text: trimmed })
    setQuestion('')
    assistant.mutate(
      {
        projectId,
        page: contextLabel,
        messages: history.slice(-MAX_HISTORY).map((message) => ({
          role: message.who === 'me' ? ('user' as const) : ('assistant' as const),
          content: message.text.slice(0, MAX_MESSAGE_CHARS),
        })),
      },
      {
        onSuccess: ({ reply }) => append({ who: 'ai', text: reply }),
        onError: (error) =>
          assistantUnavailable(error)
            ? append({ who: 'ai', text: guidanceReply(trimmed, contextLabel), note: 'General guidance: the AI assistant isn’t available right now.' })
            : append({ who: 'ai', text: 'Sorry, I couldn’t answer that.', note: errorMessage(error) }),
      },
    )
  }

  /** Sends the typed question when the form is submitted. */
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    ask(question)
  }

  /** Closes the panel on Escape without letting the key reach the page. */
  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onClose()
    }
  }

  return (
    <aside id="chat" hidden={!open} aria-labelledby={headingId} onKeyDown={onKeyDown}>
      <div className="ch-head">
        <div className="row">
          <span className="ch-dot" aria-hidden="true" />
          <b style={{ fontSize: 13 }} id={headingId}>
            NirnAIn AI
          </b>
          <span className="ch-badge">Preview</span>
          <button id="chat-close" type="button" aria-label="Close assistant" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="ch-ctx">
          <span className="gdot" aria-hidden="true" />
          Context: <span id="ch-ctx">{contextLabel}</span>
        </div>
      </div>
      <div className="ch-log" id="ch-log" role="log" aria-live="polite" ref={logRef}>
        <div className="msg ai">
          Hi{firstName ? ` ${firstName}` : ''}, I&rsquo;m NirnAIn AI. You&rsquo;re on {contextLabel}. Ask me anything about your project plan.
        </div>
        {messages.map((message) => (
          <div key={message.id} className={`msg ${message.who}`}>
            {message.text}
            {message.note && (
              <small className="muted" style={{ display: 'block', marginTop: 6, fontSize: 11 }}>
                {message.note}
              </small>
            )}
          </div>
        ))}
        {typing && <div className="msg ai">Thinking&hellip;</div>}
        {messages.length === 0 && (
          <div className="sugg">
            {ASSISTANT_SUGGESTIONS.map((suggestion) => (
              <button key={suggestion} type="button" data-sugg={suggestion} onClick={() => ask(suggestion)}>
                {suggestion}
              </button>
            ))}
          </div>
        )}
      </div>
      <form className="ch-form" id="ch-form" onSubmit={onSubmit}>
        <input
          ref={inputRef}
          id="ch-in"
          placeholder="Ask about your plan…"
          maxLength={QUESTION_MAX_CHARS}
          aria-label="Ask NirnAIn AI about your plan"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button className="btn sm" id="ch-send" type="submit" disabled={typing}>
          Send
        </button>
      </form>
    </aside>
  )
}
