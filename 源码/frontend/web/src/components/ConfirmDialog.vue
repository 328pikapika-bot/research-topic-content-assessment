<template>
  <Teleport to="body">
    <div v-if="state.visible" class="confirm-overlay" @click.self="cancel">
      <div class="confirm-dialog">
        <p class="confirm-message">{{ state.message }}</p>
        <div class="confirm-actions">
          <button class="confirm-btn confirm-btn-cancel" @click="cancel">{{ state.cancelText }}</button>
          <button class="confirm-btn confirm-btn-ok" @click="ok">{{ state.okText }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { useConfirm } from '../composables/useConfirm'

const { state, ok, cancel } = useConfirm()
</script>

<style scoped>
.confirm-overlay {
  position: fixed; inset: 0;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.45); z-index: 2000;
  animation: confirm-overlay-in 0.15s ease;
}
.confirm-dialog {
  width: 380px; max-width: calc(100vw - 40px);
  padding: 24px 24px 20px; border-radius: 8px;
  background: #fff; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.16);
  animation: confirm-dialog-in 0.2s ease;
}
.confirm-message {
  margin: 0 0 20px; font-size: 15px; color: #303133;
  line-height: 1.5; text-align: center;
}
.confirm-actions { display: flex; justify-content: center; gap: 12px; }
.confirm-btn {
  min-width: 88px; height: 34px; padding: 0 20px;
  border-radius: 4px; font-size: 14px; font-weight: 500; cursor: pointer; border: 0;
}
.confirm-btn-cancel {
  border: 1px solid #dcdfe6; background: #fff; color: #606266;
}
.confirm-btn-cancel:hover { color: #409eff; border-color: #c6e2ff; background: #ecf5ff; }
.confirm-btn-ok {
  background: #2771e0; color: #fff;
}
.confirm-btn-ok:hover {
  background: #1e60c8;
}
@keyframes confirm-overlay-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes confirm-dialog-in {
  from { opacity: 0; transform: scale(0.92) translateY(-10px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
</style>
