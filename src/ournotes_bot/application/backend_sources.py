# L3: Shared release/runtime digest inventory. Order is part of the compatibility contract.
TRANSPORT_PROTOCOL = {'major': 2, 'minor': 1}
CODE_FILES = ('backend.py', 'backend_protocol.py', 'delivery_ledger.py', 'replies.py',
              'backend_bootstrap.py', 'runtime_tasks.py', 'notice_gateway.py', 'notice_reports.py',
              'data_owner.py', 'backend_sources.py', 'admin.py', 'diagnostics.py', 'operations.py', 'operations_sources.py', 'operations_runtime.py',
              '../ai_query.py', '../natural_query/query_metrics.py', '../natural_query/query_agent.py',
              '../domain/query_policy.py', '../query/continuation.py', 'model_execution.py', 'conversation_state.py',
              'knowledge_service.py', 'knowledge_cli.py', '../domain/knowledge.py',
              '../sources/knowledge_files.py', '../sources/knowledge_embeddings.py', '../query/knowledge_retrieval.py')
CODE_FILES += ('model_runtime.py', 'persona.py', 'model_config.py', 'model_admin.py', 'usage_ledger.py', '../domain/model_calls.py',
               '../sources/model_provider.py', '../natural_query/native_tools.py', 'site_lookup.py', '../sources/haneoka/notices.py')
CODE_FILES += ('chat_agent.py','chat_context.py','chat_store.py','../domain/chat.py')
CODE_FILES += ('chat_capabilities.py',)
CODE_FILES += ('story_service.py','story_cli.py','../domain/story.py','../sources/story_files.py','../query/story_retrieval.py')
