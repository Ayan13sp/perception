# 💻 Perception UI Frontend

Modern, responsive client application for **Perception**, built with **Next.js 15 (App Router)**, **React 19**, **Tailwind CSS**, and **Radix UI / Shadcn UI**.

---

## 🎨 Frontend Architecture

The user interface delivers distinct, role-based workflows for both educators and learners:

```
perception-ui/
├── app/
│   ├── (auth)/         # Login, signup, and about routes
│   ├── (main)/         # Protected teacher & student dashboards and analytics
│   ├── layout.jsx      # Global theme & query providers
│   └── page.jsx        # Landing page with hero & feature highlights
├── components/
│   ├── dashboard/      # Role-specific dashboard views (Student & Teacher)
│   ├── layout/         # Navigation header & theme toggles
│   └── ui/             # Reusable Shadcn UI primitives (cards, dialogs, badges)
├── store/              # Client-side session state via Zustand
└── api/                # Axios instance with centralized header injection interceptors
```

---

## ✨ Features & UX Highlights

- **Dynamic Role-Based Views:** Seamlessly renders student assessment portals or teacher review stations based on authenticated role.
- **Real-Time Assessment Feedback:** Submits descriptive answers asynchronously and surfaces LLM feedback and scores with optimistic state updates.
- **Teacher Review & Override Station:** Allows teachers to inspect student responses side-by-side with model answers and manually adjust final grades.
- **Aggregated Analytics:** Live dashboard displaying submission counts, score distributions, and evaluation metrics.
- **Theme Support:** Dark and light mode out of the box via `next-themes`.

---

## 🛠️ Tech Stack

- **Framework:** [Next.js](https://nextjs.org/) (v16.x App Router) & [React 19](https://react.dev/)
- **Styling:** [Tailwind CSS v4](https://tailwindcss.com/) & [tw-animate-css](https://github.com/lucas-diniz/tw-animate-css)
- **UI Components:** [Radix UI](https://www.radix-ui.com/) primitives & [Lucide Icons](https://lucide.dev/)
- **State Management:** [Zustand](https://zustand.docs.pmnd.rs/) with localStorage persistence
- **HTTP Client:** [Axios](https://axios-http.com/) with automated auth interceptors
- **Notifications:** [Sonner](https://sonner.emilkowal.ski/) toast system

---

## 🚀 Local Development Setup

1. **Navigate to the frontend folder:**
   ```bash
   cd perception-ui
   ```

2. **Install dependencies:**
   ```bash
   npm install
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env.local
   # Ensure NEXT_PUBLIC_API_URL points to your running backend
   ```

4. **Start development server:**
   ```bash
   npm run dev
   ```

5. **Visit the app:**
   Open [http://localhost:3000](http://localhost:3000) in your browser.
