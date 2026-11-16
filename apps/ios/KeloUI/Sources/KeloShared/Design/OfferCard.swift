import SwiftUI
import KeloCore

public struct OfferCard: View {
    public let offer: Offer
    public let starting: Bool
    public let problem: OfferStart?
    public let onStart: () -> Void
    public let onDismiss: (OfferReason) -> Void
    @Environment(\.kelo) private var c

    public init(
        offer: Offer,
        starting: Bool = false,
        problem: OfferStart? = nil,
        onStart: @escaping () -> Void,
        onDismiss: @escaping (OfferReason) -> Void
    ) {
        self.offer = offer
        self.starting = starting
        self.problem = problem
        self.onStart = onStart
        self.onDismiss = onDismiss
    }

    public var body: some View {
        CardBox {
            Text(offer.sentence)
                .font(KeloFont.bodyLarge)
                .foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true)

            if !offer.needs.isEmpty {
                Text(L(R.string.offer_needs, offer.needs.joined(separator: ", ")))
                    .font(KeloFont.small)
                    .foregroundStyle(c.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if offer.hasNumbers, let checkedAt = offer.checkedAt {
                Text(L(R.string.offer_checked, RelWords.of(checkedAt)))
                    .font(KeloFont.small)
                    .foregroundStyle(c.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if problem == .busy {
                ErrorText(L(R.string.offer_busy), small: true)
            } else if problem == .failed {
                ErrorText(L(R.string.offer_failed), small: true)
            }

            HStack(spacing: 8) {
                PrimaryButton(L(R.string.offer_start), enabled: !starting) {
                    onStart()
                }

                Menu {
                    Button(L(R.string.offer_reason_not_interested)) { onDismiss(.notInterested) }
                    Button(L(R.string.offer_reason_done)) { onDismiss(.doneAlready) }
                    Button(L(R.string.offer_reason_later)) { onDismiss(.notNow) }
                } label: {
                    Text(L(R.string.offer_dismiss) + " ▾")
                        .font(KeloFont.small)
                        .foregroundStyle(c.accent)
                        .padding(.vertical, 4)
                }
            }
        }
    }
}

public struct OffersListView: View {
    public let model: OffersModel
    public let onStarted: (String) -> Void

    public init(model: OffersModel, onStarted: @escaping (String) -> Void) {
        self.model = model
        self.onStarted = onStarted
    }

    public var body: some View {
        if !model.items.isEmpty {
            VStack(spacing: 8) {
                ForEach(model.items) { o in
                    OfferCard(
                        offer: o,
                        starting: model.starting == o.id,
                        problem: model.problem?.id == o.id ? model.problem?.start : nil,
                        onStart: { model.start(o, onStarted: onStarted) },
                        onDismiss: { reason in model.dismiss(o, reason: reason) }
                    )
                }
            }
        }
    }
}
