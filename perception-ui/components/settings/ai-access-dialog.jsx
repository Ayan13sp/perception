"use client"

import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"
import { Copy, KeyRound, Trash2 } from "lucide-react"

import api, { API_BASE_URL } from "@/api/axios"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"

const MCP_URL = `${API_BASE_URL.replace(/\/$/, "")}/mcp`

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString() : "never"
}

function CopyBlock({ label, value }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      toast.success(`${label} copied`)
    } catch {
      toast.error("Copy failed. Select the text and copy it manually.")
    }
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <Button type="button" variant="ghost" size="sm" onClick={copy}>
          <Copy className="h-3.5 w-3.5" />
          Copy
        </Button>
      </div>
      <pre className="whitespace-pre-wrap break-all rounded-md border bg-muted p-2 font-mono text-xs">{value}</pre>
    </div>
  )
}

export function AiAccessDialog({ open, onOpenChange }) {
  const [tokens, setTokens] = useState([])
  const [name, setName] = useState("Claude Code")
  const [created, setCreated] = useState(null)
  const [busy, setBusy] = useState(false)

  const loadTokens = useCallback(async () => {
    try {
      const res = await api.get("/auth/tokens")
      setTokens(res.data)
    } catch {
      toast.error("Failed to load tokens.")
    }
  }, [])

  useEffect(() => {
    if (open) {
      setCreated(null)
      loadTokens()
    }
  }, [open, loadTokens])

  const createToken = async (event) => {
    event.preventDefault()
    setBusy(true)
    try {
      const res = await api.post("/auth/tokens", { name: name.trim() })
      setCreated(res.data)
      loadTokens()
    } catch (error) {
      toast.error(error.response?.data?.detail || "Failed to create token.")
    } finally {
      setBusy(false)
    }
  }

  const revokeToken = async (id) => {
    try {
      await api.delete(`/auth/tokens/${id}`)
      setTokens((current) => current.filter((t) => t.id !== id))
      toast.success("Token revoked")
    } catch (error) {
      toast.error(error.response?.data?.detail || "Failed to revoke token.")
    }
  }

  const claudeCommand = created
    ? `claude mcp add --transport http perception ${MCP_URL} --header "Authorization: Bearer ${created.token}"`
    : ""
  const cursorConfig = created
    ? JSON.stringify(
        { mcpServers: { perception: { url: MCP_URL, headers: { Authorization: `Bearer ${created.token}` } } } },
        null,
        2
      )
    : ""

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Connect an AI assistant</DialogTitle>
          <DialogDescription>
            Create a personal access token to use Perception from Claude Code, Cursor or any MCP client.
            The token acts as you, so keep it secret.
          </DialogDescription>
        </DialogHeader>

        {created ? (
          <div className="space-y-3">
            <p className="text-sm font-medium text-amber-600 dark:text-amber-400">
              Copy this token now. You won&apos;t be able to see it again.
            </p>
            <CopyBlock label="Token" value={created.token} />
            <CopyBlock label="Claude Code" value={claudeCommand} />
            <CopyBlock label="Cursor (~/.cursor/mcp.json)" value={cursorConfig} />
            <Button variant="outline" className="w-full" onClick={() => setCreated(null)}>
              Done
            </Button>
          </div>
        ) : (
          <form onSubmit={createToken} className="flex items-end gap-2">
            <div className="grid flex-1 gap-1.5">
              <Label htmlFor="token-name">Token name</Label>
              <Input
                id="token-name"
                value={name}
                maxLength={60}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            <Button type="submit" disabled={busy || !name.trim()}>
              <KeyRound className="h-4 w-4" />
              {busy ? "Creating..." : "Create token"}
            </Button>
          </form>
        )}

        <Separator />

        <div className="space-y-2">
          <p className="text-sm font-medium">Active tokens</p>
          {tokens.length === 0 ? (
            <p className="text-sm text-muted-foreground">No active tokens.</p>
          ) : (
            tokens.map((t) => (
              <div key={t.id} className="flex items-center justify-between gap-2 rounded-md border p-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{t.name}</p>
                  <p className="font-mono text-xs text-muted-foreground">
                    {t.prefix}… · created {formatDate(t.created_at)} · last used {formatDate(t.last_used_at)} ·
                    expires {formatDate(t.expires_at)}
                  </p>
                </div>
                <Button variant="ghost" size="icon" aria-label={`Revoke ${t.name}`} onClick={() => revokeToken(t.id)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
