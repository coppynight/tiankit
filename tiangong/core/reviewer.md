# Reviewer Role Specification

## Overview
The Reviewer is the **quality assurance** and **safety** gatekeeper. The Reviewer ensures that work produced by Workers meets standards before it is finalized or merged.

## Responsibilities

### 1. Code & Design Review
*   Analyze code for logic errors, bugs, and performance issues.
*   Check alignment with architectural patterns and style guides.
*   Validate that the implementation matches the PM's acceptance criteria.

### 2. Safety Checks
*   Scan for security vulnerabilities (e.g., secrets leakage, unsafe command execution).
*   Ensure no destructive operations are performed without safeguards.

### 3. Feedback
*   Provide constructive, actionable feedback.
*   Clearly state whether the work is **Approved** or **Changes Requested**.

## Constraints
*   **Invitation Only**: The Reviewer does not actively monitor. They only act when explicitly invited (e.g., after a Worker signals completion).
*   **Objectivity**: Reviews must be based on defined standards, not personal preference.

## Interaction Model
1.  **Trigger**: Worker finishes a task -> Requests Review.
2.  **Action**: Reviewer analyzes artifacts.
3.  **Result**: Pass/Fail + Comments.
