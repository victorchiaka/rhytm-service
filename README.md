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

*(Note: Global insights have been shifted to the next version. We are currently focusing on Data Export and AI integration).*

---

## 2. Routine Model (`routines`)

**Core Responsibility:** Grouping habits by specific times of day (e.g., "Morning Workflow", "Evening Wind Down").

*(Note: Routine completion rates and insights have been shifted to the next version).*

---

## 3. Habit Model (`habits`)

**Core Responsibility:** Individual actionable items that need to be tracked, linked to routines.

*(Note: Streaks, consistency, and improvement calculations are slated for the next version).*

---

## 4. Activity Log Model (`activity_log`)

**Core Responsibility:** Immutable ledger of when habits were completed.

*(Note: Basic activity logging via `/sync-activity` is complete and handles check-ins/undos natively).*

### Features & Endpoints
- **Aggregates (Data for the Frontend)**
  - `GET /activity/calendar` - Fetch a heat-map array (like GitHub contributions) for the current month to show days where all/most habits were completed.

---

## 5. Next Version Features (v2.0)

**Global Insights & Analytics**
- **Global Aggregates**: `GET /users/insights/summary` - Aggregate of user performance.
- **Completion Rate**: `GET /routines/{id}/completion-rate` 
- **Routine Insights**: `GET /routines/insights`

**Advanced Habit Features & Notifications**
- **Advanced Push Notifications**: `POST /habits/sync-reminders` (Primary reminders will be handled locally by the OS scheduler (iOS/Android). Firebase Cloud Messaging is kept here strictly for future advanced cloud-triggered scenarios).
- **Streaks**: `GET /habits/{id}/streak`
- **Consistency**: `GET /habits/{id}/consistency`
- **Improvement**: `GET /habits/{id}/improvement`

**Backlog / Future Enhancements**
- **Health Integration**: Sync with Apple Health / Google Fit to automatically complete physical habits (e.g., Steps, Sleep).
- **Advanced Gamification**: Badges, levels, and achievements based on long-term consistency.
- **Home Screen Widgets**: iOS/Android widget support to show daily progress.

---

## Release Timeline & Pricing Strategy

This phased approach allows for gradual feature rollout and justified price bumps as the product's value increases.

### **Phase 1: v1.0 - The Foundation & Insights (Current Focus)**
- **Features**: Smart Habits, Routine organization, core Activity Logging, AI Integration, Subscriptions, and Data Export (Excel/PDF).
- **Pricing**:
  - Free Tier: Basic streak tracking, limited routines.
  - Premium Tier (Introductory): **$4.99 / month**
    - Unlimited routines.
    - AI-Driven Suggestions & Data Exports.

### **Phase 2: v2.0 - Data & Ecosystem Update**
- **Features**: Global Insights, Streaks & Consistency calculations, Firebase Push Notifications (Advanced), Home Screen Widgets, Health Integration (Apple/Google), and Advanced Gamification.
- **Pricing Action**: Price Bump!
  - Premium Tier increases to **$7.99 / month**.
  - Justification: The app is now a complete automated life-management tool.

---

## Implementation Checklist (v1.0 Release)
*These tasks must be completed before shipping the initial release.*

- [x] Implement Smart Habits and Routine organization.
- [x] Implement core Activity Logging, syncing, and check-ins/undos.
- [x] Setup basic Subscriptions and Paywall Benefits.
- [x] Implement AI Integration and Suggestions.
- [x] Create PDF/Excel Data Export utilities.
- [ ] Setup RevenueCat webhooks and validate premium status in middleware.

### Next Version (v2.0) Checklist
*These tasks are reserved for the next major update.*

- [ ] Global Insights & Aggregates.
- [ ] Routine Analytics (Completion Rate & Insights).
- [ ] Streaks, Consistency, and Improvement calculations.
- [ ] Firebase Push Notifications for cloud-triggered scenarios.
- [ ] Health Integration (Apple/Google).
- [ ] Advanced Gamification.
- [ ] Home Screen Widgets.
- [ ] Better visually appealing PDF report with a custom watermark/logo.
