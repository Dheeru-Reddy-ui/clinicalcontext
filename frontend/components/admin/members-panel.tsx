"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { useAuth } from "@/components/providers/auth-provider";
import { RequireRole } from "@/components/auth/require-role";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ApiError,
  apiFetch,
  type InviteOut,
  type MemberOut,
  type OrgRole,
} from "@/lib/api";

const ROLES: OrgRole[] = ["owner", "clinician", "viewer"];

export function MembersPanel() {
  const { session, me, role } = useAuth();
  const [members, setMembers] = useState<MemberOut[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<OrgRole>("clinician");
  const [invitePending, setInvitePending] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [createdInvite, setCreatedInvite] = useState<InviteOut | null>(null);
  const [roleError, setRoleError] = useState<string | null>(null);

  const loadMembers = useCallback(async () => {
    if (!session) return;
    try {
      const payload = await apiFetch<{ members: MemberOut[] }>(
        "/api/v1/orgs/members",
        { accessToken: session.access_token },
      );
      setMembers(payload.members);
      setLoadError(null);
    } catch (error) {
      setLoadError(
        error instanceof ApiError ? error.message : "Could not load members.",
      );
    }
  }, [session]);

  useEffect(() => {
    void loadMembers();
  }, [loadMembers]);

  async function createInvite(event: FormEvent) {
    event.preventDefault();
    if (!session) return;
    setInvitePending(true);
    setInviteError(null);
    setCreatedInvite(null);
    try {
      const invite = await apiFetch<InviteOut>("/api/v1/orgs/invites", {
        method: "POST",
        body: { email: inviteEmail, role: inviteRole },
        accessToken: session.access_token,
      });
      setCreatedInvite(invite);
      setInviteEmail("");
    } catch (error) {
      setInviteError(
        error instanceof ApiError ? error.message : "Invite failed.",
      );
    } finally {
      setInvitePending(false);
    }
  }

  async function changeRole(memberId: string, newRole: OrgRole) {
    if (!session) return;
    setRoleError(null);
    try {
      await apiFetch<MemberOut>(`/api/v1/orgs/members/${memberId}/role`, {
        method: "PATCH",
        body: { role: newRole },
        accessToken: session.access_token,
      });
      await loadMembers();
    } catch (error) {
      setRoleError(
        error instanceof ApiError ? error.message : "Role change failed.",
      );
      await loadMembers(); // restore the select to server truth
    }
  }

  const inviteLink = createdInvite?.token
    ? `${typeof window !== "undefined" ? window.location.origin : ""}/signup?invite=${encodeURIComponent(createdInvite.token)}`
    : null;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Members</h1>
        <p className="text-sm text-muted-foreground">
          Everyone in your organization. Roles: owner (admin), clinician (can
          ask), viewer (read-only).
        </p>
      </div>

      <RequireRole roles={["owner"]}>
        <Card>
          <CardHeader>
            <CardTitle>Invite someone</CardTitle>
            <CardDescription>
              They&apos;ll join this organization with the role you pick.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <form
              onSubmit={createInvite}
              className="flex flex-col gap-4 sm:flex-row sm:items-end"
            >
              <div className="flex flex-1 flex-col gap-2">
                <Label htmlFor="inviteEmail">Email</Label>
                <Input
                  id="inviteEmail"
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  required
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label>Role</Label>
                <Select
                  value={inviteRole}
                  onValueChange={(value) => setInviteRole(value as OrgRole)}
                >
                  <SelectTrigger className="w-36">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLES.map((r) => (
                      <SelectItem key={r} value={r}>
                        {r}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button type="submit" disabled={invitePending}>
                {invitePending ? "Creating…" : "Create invite"}
              </Button>
            </form>
            {inviteError && (
              <p className="text-sm text-destructive">{inviteError}</p>
            )}
            {createdInvite && inviteLink && (
              <div className="rounded-lg border bg-muted/40 p-3 text-sm">
                <p className="font-medium">
                  Invite created for {createdInvite.email} ({createdInvite.role})
                </p>
                <p className="mt-1 text-muted-foreground">
                  Share this link — the token is shown only once:
                </p>
                <code className="mt-2 block overflow-x-auto rounded bg-muted p-2 text-xs">
                  {inviteLink}
                </code>
              </div>
            )}
          </CardContent>
        </Card>
      </RequireRole>

      <Card>
        <CardHeader>
          <CardTitle>Current members</CardTitle>
          {roleError && <p className="text-sm text-destructive">{roleError}</p>}
        </CardHeader>
        <CardContent>
          {loadError ? (
            <p className="text-sm text-destructive">{loadError}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Joined</TableHead>
                  <TableHead>Role</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((member) => (
                  <TableRow key={member.user_id}>
                    <TableCell className="font-medium">
                      {member.full_name ?? "—"}
                      {member.user_id === me?.user_id && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          (you)
                        </span>
                      )}
                    </TableCell>
                    <TableCell>{member.email ?? "—"}</TableCell>
                    <TableCell>
                      {new Date(member.joined_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell>
                      {role === "owner" ? (
                        <Select
                          value={member.role}
                          onValueChange={(value) =>
                            void changeRole(member.user_id, value as OrgRole)
                          }
                        >
                          <SelectTrigger className="w-32">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {ROLES.map((r) => (
                              <SelectItem key={r} value={r}>
                                {r}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Badge variant="secondary">{member.role}</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
