import React, { createContext, useContext, useMemo, useState } from 'react';
import { useLive } from './live';
import DisposalDialog from '../components/DisposalDialog';
import ReceiveStockDialog from '../components/ReceiveStockDialog';
import ConfirmLocationDialog from '../components/ConfirmLocationDialog';
import UploadRecordingDialog from '../components/UploadRecordingDialog';
import ViewReviewDialog from '../components/ViewReviewDialog';
import DisposeBatchDialog from '../components/DisposeBatchDialog';
import AddPrescriptionDialog from '../components/AddPrescriptionDialog';

const DialogContext = createContext(null);

export function DialogProvider({ children }) {
  const { state } = useLive();
  const [dialog, setDialog] = useState(null);
  const close = () => setDialog(null);

  const api = useMemo(
    () => ({
      openDisposal: (disposalId) => setDialog((d) => d ?? { type: 'disposal', disposalId }),
      openReceive: (medicationKey) => setDialog({ type: 'receive', medicationKey }),
      openConfirm: (alertId) => setDialog({ type: 'confirm', alertId }),
      openUpload: () => setDialog({ type: 'upload' }),
      openViewReview: (recording) => setDialog({ type: 'view', recording }),
      openDisposeBatch: (receiptId) => setDialog({ type: 'dispose-batch', receiptId }),
      openAddPrescription: () => setDialog({ type: 'prescription' }),
    }),
    [],
  );

  let content = null;
  if (dialog?.type === 'upload') {
    content = <UploadRecordingDialog onClose={close} onUploaded={close} />;
  } else if (dialog?.type === 'view') {
    content = <ViewReviewDialog key={dialog.recording.name} recording={dialog.recording} onClose={close} />;
  } else if (dialog && state) {
    if (dialog.type === 'disposal') {
      const disposal = state.disposals[dialog.disposalId];
      if (disposal?.status === 'pending_employee_entry') {
        content = <DisposalDialog key={disposal.disposal_id} disposal={disposal} onClose={close} />;
      }
    } else if (dialog.type === 'receive') {
      content = <ReceiveStockDialog medicationKey={dialog.medicationKey} onClose={close} />;
    } else if (dialog.type === 'confirm') {
      const alert = state.alerts[dialog.alertId];
      if (alert?.status === 'open') content = <ConfirmLocationDialog alert={alert} onClose={close} />;
    } else if (dialog.type === 'dispose-batch') {
      const receipt = state.receipts.find((r) => r.receipt_id === dialog.receiptId);
      if (receipt) content = <DisposeBatchDialog receipt={receipt} onClose={close} />;
    } else if (dialog.type === 'prescription') {
      content = <AddPrescriptionDialog onClose={close} />;
    }
  }

  return (
    <DialogContext.Provider value={api}>
      {children}
      {content}
    </DialogContext.Provider>
  );
}

export function useDialogs() {
  return useContext(DialogContext);
}
