// MongoDB initializes this script in MONGO_INITDB_DATABASE on first volume creation.
// Keep database and collection names aligned with backend/app/core/config.py.

db.content_assessment_tasks.createIndex(
  { user_scope: 1, created_at: -1 },
  { name: "idx_user_scope_created_at" }
);
db.content_assessment_tasks.createIndex(
  { user_scope: 1, status: 1, created_at: -1 },
  { name: "idx_user_scope_status_created_at" }
);
db.content_assessment_tasks.createIndex(
  { user_scope: 1, title: "text", task_type: "text", raw_user_input: "text" },
  { name: "idx_user_scope_text_search" }
);
db.content_assessment_tasks.createIndex(
  { task_id: 1 },
  { name: "idx_task_id", unique: true }
);
db.content_assessment_tasks.createIndex(
  { task_id: 1, user_scope: 1 },
  { name: "idx_task_id_user_scope", unique: true }
);

db.content_assessment_task_events.createIndex(
  { task_id: 1, created_at: 1 },
  { name: "idx_task_id_created_at" }
);
db.content_assessment_task_events.createIndex(
  { user_scope: 1, task_id: 1 },
  { name: "idx_user_scope_task_id" }
);

db.content_assessment_parsed_files.createIndex(
  { user_scope: 1, created_at: -1 },
  { name: "idx_parsed_file_user_scope_created_at" }
);
db.content_assessment_parsed_files.createIndex(
  { user_scope: 1, file_id: 1 },
  { name: "idx_parsed_file_user_scope_file_id", unique: true }
);

db.content_assessment_feedback.createIndex(
  { user_scope: 1, created_at: -1 },
  { name: "idx_feedback_user_scope_created_at" }
);
db.content_assessment_feedback.createIndex(
  { feedback_id: 1 },
  { name: "idx_feedback_id", unique: true }
);

print(`ContentAssessment indexes ensured for database: ${db.getName()}`);
