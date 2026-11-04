<template>
  <Teleport to="body">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      class="top-toast"
      :class="'toast-' + toast.type"
      @click="remove(toast.id)"
    >
      {{ toast.message }}
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { useToast } from '../composables/useToast'

const { toasts, remove } = useToast()
</script>

<style scoped>
.top-toast {
  position: fixed;
  left: 50%;
  top: 24px;
  z-index: 1000;
  transform: translateX(-50%);
  padding: 10px 20px;
  border-radius: 999px;
  background: rgba(35, 46, 90, 0.92);
  box-shadow: 0 14px 28px rgba(24, 29, 54, 0.2);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  animation: toast-in 0.25s ease;
}

.toast-success {
  background: rgba(34, 139, 34, 0.92);
}

.toast-error {
  background: rgba(220, 38, 38, 0.92);
}

.toast-info {
  background: rgba(35, 46, 90, 0.92);
}

@keyframes toast-in {
  from { opacity: 0; transform: translateX(-50%) translateY(-10px); }
  to { opacity: 1; transform: translateX(-50%) translateY(0); }
}
</style>
