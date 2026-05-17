"use client";

import { useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
	SheetTrigger,
} from "@ui/components/ui/sheet";
import { UserPlusIcon, UsersIcon } from "lucide-react";
import { useState } from "react";
import { AssigneeAvatar } from "@/components/asignee-avatar";
import { MemberInviteForm } from "@/components/forms/member-invite-form";
import { trpc } from "@/utils/trpc";

type Props = { projectId: string; team: string };

type Member = {
	id: string | null;
	name: string | null;
	email: string | null;
	image: string | null;
	color: string | null;
	createdAt: string | null;
};

/**
 * Project-scoped Members tab — grid of avatar + name + email + role for
 * everyone in `projectMembers`. The project lead (separate field on the
 * project row) gets the "Lead" badge.
 */
export function ProjectMembersView({ projectId }: Props) {
	const projectQuery = useQuery(
		trpc.projects.getById.queryOptions({ id: projectId } as any),
	);
	const membersQuery = useQuery(
		trpc.projects.getMembers.queryOptions({ projectId }),
	);
	const project = projectQuery.data as
		| { name?: string; leadId?: string | null }
		| undefined;
	const members = (membersQuery.data ?? []) as Member[];
	const [inviteOpen, setInviteOpen] = useState(false);

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							{project?.name ?? "Project"} — Members
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							Everyone with access to this project. ({members.length})
						</p>
					</div>
					<Sheet open={inviteOpen} onOpenChange={setInviteOpen}>
						<SheetTrigger asChild>
							<Button variant="outline" size="sm">
								<UserPlusIcon className="size-3.5" />
								Add member
							</Button>
						</SheetTrigger>
						<SheetContent className="px-4">
							<SheetHeader className="px-0">
								<SheetTitle>Invite a teammate</SheetTitle>
								<SheetDescription>
									New invitees join the workspace first, then can be assigned to{" "}
									{project?.name ?? "this project"} from the sidebar or task
									picker.
								</SheetDescription>
							</SheetHeader>
							<div className="px-0 pb-4">
								<MemberInviteForm />
							</div>
						</SheetContent>
					</Sheet>
				</div>
			</header>
			<div className="grow overflow-y-auto px-6 py-4">
				{membersQuery.isLoading && (
					<div className="text-[12px] text-muted-foreground">Loading…</div>
				)}
				{members.length === 0 && !membersQuery.isLoading && (
					<div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
						<UsersIcon className="size-10 text-muted-foreground" />
						<div>
							<p className="font-[510] text-foreground text-sm">
								No members yet
							</p>
							<p className="mt-0.5 text-[12px] text-muted-foreground">
								Invite teammates to collaborate on{" "}
								{project?.name ?? "this project"}. They'll see it in their
								sidebar.
							</p>
						</div>
						<Button
							size="sm"
							onClick={() => setInviteOpen(true)}
							className="mt-2"
						>
							<UserPlusIcon className="size-3.5" />
							Add member
						</Button>
					</div>
				)}
				<ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
					{members.map((m) => {
						const isLead = !!project?.leadId && project.leadId === m.id;
						return (
							<li
								key={m.id ?? `${m.email}`}
								className="flex items-center gap-3 rounded-md border border-border bg-card/40 px-3 py-2.5"
							>
								<AssigneeAvatar
									name={m.name}
									email={m.email}
									image={m.image}
									color={m.color}
									className="size-9"
								/>
								<div className="min-w-0 grow">
									<div className="flex items-center gap-2">
										<span className="truncate font-medium text-sm">
											{m.name ?? m.email ?? "Unknown"}
										</span>
										{isLead && (
											<span className="rounded bg-primary/10 px-1.5 py-0.5 font-[510] text-[10px] text-primary uppercase tracking-wider">
												Lead
											</span>
										)}
									</div>
									{m.email && (
										<div className="truncate text-muted-foreground text-xs">
											{m.email}
										</div>
									)}
								</div>
							</li>
						);
					})}
				</ul>
			</div>
		</div>
	);
}
