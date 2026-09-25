# Rhytm Service - Feature Roadmap

This service goes beyond simple CRUD operations for habit tracking. It focuses on solving real problems by measuring consistency, calculating improvements, and providing actionable insights, alongside premium features like data export and push notifications.

Below is an organized list of features, endpoints, and calculations for each model in the system.

---

## Development Commands

### Database

```bash
# Apply migrations
uv run alembic upgrade head

# Seed to the database
python -m db.migration
```

### Running the Server

```bash
# Development
uv run fastapi dev

# Production
uv run uvicorn main:app --host 0.0.0.0 --port 8931 --workers 4
```

---


## 1. User Model (`users`)

**Core Responsibility:** Account management, authentication, subscriptions, and global user insights.

### Features & Endpoints
- **Data Export (Pro)**
  - `GET /users/export/excel` - Generate an Excel file containing all routines, habits, and activity logs.
  - `GET /users/export/pdf` - Generate a visually appealing PDF report of user consistency and improvements with a custom watermark/logo.
- **Global Aggregates**
  - `GET /users/insights/summary` - Returns an aggregate of user performance across all routines and habits (e.g., overall consistency score, total days active).

---

## 2. Routine Model (`routines`)

**Core Responsibility:** Grouping habits by specific times of day (e.g., "Morning Workflow", "Evening Wind Down").

### Features & Endpoints
- **Routine Analytics**
  - `GET /routines/{id}/completion-rate` - Calculate the completion percentage of all habits within this routine for a given date range.
  - `GET /routines/insights` - List all routines ordered by their consistency scores (which routines is the user best/worst at?).

---

## 3. Habit Model (`habits`)

**Core Responsibility:** Individual actionable items that need to be tracked, linked to routines.

### Features & Endpoints
- **Push Notifications**
  - `POST /habits/sync-reminders` - Sync `reminder_time` and `days_of_week` with a Firebase Cloud Messaging worker to push notifications dynamically.
- **Performance & Improvement Calculations**
  - `GET /habits/{id}/streak` - Calculate the current and longest continuous streak based on `ActivityLog`.
  - `GET /habits/{id}/consistency` - Calculate consistency percentage over the last 30/60/90 days.
  - `GET /habits/{id}/improvement` - Compare the consistency of the current month vs. the previous month (e.g., "You are 15% more consistent this month").

---

## 4. Activity Log Model (`activity_log`)

**Core Responsibility:** Immutable ledger of when habits were completed.

### Features & Endpoints
- **Tracking**
  - `POST /activity/log` - Mark a habit as completed for a specific `activity_date`.
  - `DELETE /activity/log` - Undo a completion (un-mark a habit).
- **Aggregates (Data for the Frontend)**
  - `GET /activity/calendar` - Fetch a heat-map array (like GitHub contributions) for the current month to show days where all/most habits were completed.

## 5. Upcoming Advanced Features (Backlog)
- **Health Integration**: Sync with Apple Health / Google Fit to automatically complete physical habits (e.g., Steps, Sleep).
- **Advanced Gamification**: Badges, levels, and achievements based on long-term consistency (e.g., "100-Day Streak Club").
- **Home Screen Widgets**: iOS/Android widget support to show daily progress at a glance without opening the app.

---

## Release Timeline & Pricing Strategy

This phased approach allows for gradual feature rollout and justified price bumps as the product's value increases.

### **Phase 1: v1.0 - The Foundation & Insights (Current Focus)**
- **Features**: Smart Habits, Routine organization, core Activity Logging, Streak/Consistency calculations, AI-Driven Suggestions, Push Notifications (Firebase), and Global User Aggregates.
- **Pricing**:
  - Free Tier: Basic streak tracking, limited routines.
  - Premium Tier (Introductory): **$4.99 / month**
    - Unlimited routines.
    - AI-Driven Suggestions & Advanced Insights.

### **Phase 2: v2.0 - Data & Ecosystem Update**
- **Features**: Excel/PDF data exports with custom watermarks, Home Screen Widgets, Health Integration (Apple/Google), and Advanced Gamification.
- **Pricing Action**: Price Bump!
  - Premium Tier increases to **$7.99 / month**.
  - Justification: The app is now a complete automated life-management tool with comprehensive data export capabilities.

---

## Technical Debt & Immediate Tasks
- [ ] Implement Firebase Push Notification worker.
- [ ] Setup RevenueCat webhooks and validate premium status in middleware.
- [ ] Create PDF/Excel generation utility functions.
- [ ] Write complex SQL queries / SQLAlchemy expressions for consistency calculations.

---

## 6. Subscriptions (Future / Admin Portal)

- **`POST /subscriptions/benefits`** — Add/update paywall feature entries (e.g. feature name, basic value, pro value).
  Requires a private admin portal (owner-only). Do NOT expose publicly. This will replace the current hardcoded seed in `populate_benefits()`.
  The portal will be a separate internal tool — implement when the admin dashboard is ready.
