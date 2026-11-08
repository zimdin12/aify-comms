# Working an issue

Applies to every fix under the [stack roadmap](../ROADMAP.md), in every repository.

The 2026-11 audit issues were written by a reviewer who read the code but not its history.
An issue's proposed fix is a hypothesis. Code that looks wrong is often a guard for a case
the reviewer never saw, and removing it is how a fix becomes a regression.

## Scope first

Each repository, component and file owns one responsibility
([ROLES.md](../audit-2026-11/ROLES.md)), and each issue targets one of them. The reviewer's
acceptance criteria and option text are proposals too: on 2026-11-07 two small wrapper fixes
(a log name, a settings-file location) were planned against those criteria as a shared
ownership mechanism, grew to 31 changed files without the requested change being made, and
were reverted.

No overbuilding. Solve real issues: a failure seen on the system, not a case constructed in
review. Make only real improvements. Each repository, file and function has one goal and
concern, and a change stays inside the one it serves.

0. Before planning, write three lines: the responsibility the issue targets and its owner
   (repository, component, file); that responsibility's goal; the smallest change that meets
   it. Keep only the acceptance criteria that goal needs. Move the rest to a separate issue,
   or drop them, and say which in the plan.
   - One issue, one change. Do not bundle issues to design a shared mechanism.
   - A plan larger than the issue's effort label, or one that changes another component's
     responsibility, goes back for scope before approval.
   - The plan names a size budget. If the build needs more, stop and ask; do not extend.

## Before writing code

1. Read the issue body and every comment, including the owner's verdict comment ("Agree
   with changes" names the changes) and the issue's row in the
   [issue map](2026-11-04-issue-map.md), which holds its exit criterion.
2. Find out why the current code is the way it is: `DECISIONS.md`, the audit `ROLES.md`
   and `INTENT.md`, `git log -S` and `git blame` on the lines you will change, and the
   tests that pin them. List the behaviours the current code protects.
3. Write a short plan into the review request: the defect, the protected behaviours, the
   change, and the test that would fail if the change were wrong.

Stop and ask the reviewer before building when the fix would change a recorded decision,
when the issue's proposal conflicts with the owner's comment, or when the fix outgrows its
issue.

## Building

4. Red first. Write the test for the defect and watch it fail on current main. For each
   protected behaviour, keep or add a control test that passes before and after.
5. Make the smallest change, in the module that owns the subject.
6. Run the targeted tests, including the suites in other repositories that read what you
   changed. No single run over ten minutes on the shared host.
7. Do not install, restart or relaunch anything. Fixes are built and reviewed first, then
   adopted together. The globally linked checkouts stay where they are until then.

## Handing it to review

8. Keep each issue's change in its own commit. The message says what changed and why, in
   plain words, with `refs #N`; leave closing issues to the operator. No agent names and no
   AI trailers.
9. Send the reviewer the issue link, the protected-behaviour list, the red and green runs
   (command and exit status), and the commit. The reviewer reads the issue thread as well
   as the diff. Push after approval.
