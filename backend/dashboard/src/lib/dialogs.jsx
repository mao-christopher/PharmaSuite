import React, { createContext, useContext, useMemo, useState } from 'react';
import { useLive } from './live';
import DisposalDialog from '../components/DisposalDialog';
import ReceiveStockDialog from '../components/ReceiveStockDialog';
import ConfirmLocationDialog from '../components/ConfirmLocationDialog';
import UploadRecordingDialog from '../components/UploadRecordingDialog';

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
    }),
    [],
  );

  let content = null;
  if (dialog?.type === 'upload') {
    content = <UploadRecordingDialog onClose={close} />;
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
